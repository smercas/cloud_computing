from datetime import datetime
import os

from dotenv import load_dotenv
load_dotenv()
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.ext.automap import automap_base, AutomapBase
from sqlalchemy.orm import Session
from identity.flask import Auth
from flask import (	Flask, redirect, render_template, request,
										send_from_directory, url_for)

from backend import utils

from azure.storage.blob import BlobServiceClient
import webbrowser

from backend.key_vault import KeyVault
key_vault = KeyVault(default_transform=utils.to_value)

AZURE_CONNECTION_STRING = " DefaultEndpointsProtocol=https;AccountName=calendarappstoragecloud;AccountKey=40Qtdu5nPtczY4yg8wbWcNVnLiYWBCkaDGbQ3fMvBQ1/lgkEuKRC9rV+/UM6utw38Jp8PjUTemsr+AStCeFD6g==;EndpointSuffix=core.windows.net" # DON'T PUT SECRETS HERE BTW; if you're sure abt using some secrets, you can add them to the env in github > setting > secrets and variables
CONTAINER_NAME = "event-files"
blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
container_client = blob_service_client.get_container_client(CONTAINER_NAME)

def upload_file_to_container(file_name, file_path):
	# Upload the file to the container
	with open(file_path, "rb") as data:
		container_client.upload_blob(name=file_name, data=data)
		print(f"File {file_name} uploaded to container {CONTAINER_NAME}.")

app = Flask(__name__)
app.config.from_object('app_config')
app.config["SQLALCHEMY_DATABASE_URI"] = key_vault["database-uri"]
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

auth = Auth(
	app,
	client_id=										key_vault["app-registration-client-id"],
	client_credential=						key_vault["app-registration-client-secret"],
	redirect_uri=									os.environ["redirect_uri"],
	b2c_tenant_name=							key_vault["b2c-tenant-name"],
	b2c_signup_signin_user_flow=	key_vault['b2c-sign-up-and-sign-in-user-flow'],
	b2c_edit_profile_user_flow=		key_vault['b2c-edit-profile-user-flow'],
	b2c_reset_password_user_flow=	key_vault['b2c-reset-password-user-flow'],
)

db = SQLAlchemy(app)
base: AutomapBase = automap_base()
with app.app_context():
	base.prepare(db.engine)

users = base.classes.users
events = base.classes.events
notes = base.classes.notes
reminders = base.classes.reminders
file_attachments = base.classes.file_attachments

events.field_transform_pairs = {
	'title':				utils.identity,
	'start_date':		datetime.fromisoformat,
	'end_date':			datetime.fromisoformat,
	'location':			utils.identity,
	'description':	utils.identity,
} # im too lazy to do reflection on the object and figure out a cleaner way of doing this

notes.field_transform_pairs = {
	"content": utils.identity,
}

def apply_data_to_automap(o, data: dict[str, str]):
	for field, transform in o.__class__.field_transform_pairs.items():
		v = data.get(field, None)
		if v is None: continue
		setattr(o, field, transform(v))

events.apply = apply_data_to_automap
notes.apply = apply_data_to_automap

def automap_to_dict(o):
	res = {f: getattr(o, f) for f in o.__class__.field_transform_pairs.keys()}
	res["id"] = o.id
	return {k: v for k, v in res.items() if v is not None}

@app.route("/events", methods=["GET"])
@auth.login_required
def get_events(*, context):
	session = Session(db.engine)
	events = session.query(events).filter(events.user_id == context["user"]["oid"]).all()
	return { "events": list(map(automap_to_dict, events)) }

@app.route("/events", methods=["POST"])
@auth.login_required
def create_event(*, context):
	data = request.json
	session = Session(db.engine)
	event = events(
		title=data['title'],
		start_time=datetime.fromisoformat(data['start_time']),
		end_time=datetime.fromisoformat(data['end_time']),
		location=data.get('location', None),
		description=data.get('description', None),
		user_id=context["user"]["oid"]
	)
	session.add(event)
	session.commit()
	return automap_to_dict(event), 201

@app.route("/events/<id>", methods=["GET"])
@auth.login_required
def get_event(id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=id, user_id=context["user"]["oid"]).first()
	if not event:
		return {"error": "Event not found"}, 404
	return automap_to_dict(event), 200

@app.route("/events/<event_id>", methods=["PUT"])
@auth.login_required
def update_event(event_id, *, context):
	data = request.json
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if not event:
		return {"error": "Event not found"}, 404
	apply_data_to_automap(event, data)
	# event.apply(data) #TODO: see if this works
	session.commit()
	return {}, 200

@app.route("/events/<event_id>", methods=["DELETE"])
@auth.login_required
def delete_event(event_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if not event:
		return {"error": "Event not found"}, 404
	session.delete(event)
	session.commit()
	return {}, 204

@app.route("/events/<event_id>/notes", methods=["GET"])
@auth.login_required
def get_notes(event_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if not event:
		return {"error": "Event not found"}, 404
	notes = session.query(notes).filter_by(event_id=event_id).all()
	return {"notes": list(map(automap_to_dict, notes))}, 200

@app.route("/events/<event_id>/notes", methods=["POST"])
@auth.login_required
def add_note(event_id, *, context):
	data = request.json
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if not event:
		return {"error": "Event not found"}, 404
	note = notes(event_id=event_id, content=data['content'])
	session.add(note)
	session.commit()
	return automap_to_dict(note), 201

@app.route("/events/<event_id>/notes/<note_id>", methods=["PUT"])
@auth.login_required
def update_note(event_id, note_id, *, context):
	data = request.json
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if not event:
		return {"error": "Event not found"}, 404
	note = session.query(notes).get(note_id)
	if not note or note.event_id != event_id:
		return {"error": "Note not found"}, 404
	apply_data_to_automap(note, data)
	# note.apply(data) #TODO: see if this works
	session.commit()
	return {}, 200

@app.route("/events/<event_id>/notes/<note_id>", methods=["DELETE"])
@auth.login_required
def delete_note(event_id, note_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if not event:
		return {"error": "Event not found"}, 404
	note = session.query(notes).get(note_id)
	if not note or note.event_id != event_id:
		return {"error": "Note not found"}, 404
	session.delete(note)
	session.commit()
	return {}, 204





@app.route('/')
@auth.login_required
def index(*, context):
	return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
	return send_from_directory(os.path.join(app.root_path, 'static'),
							'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/hello', methods=['POST'])
def hello():
	name = request.form.get('name')

	if name:
		return render_template('hello.html', name = name)
	return redirect(url_for('index'))

@app.route("/logout")
def logout():
	tenant = key_vault["b2c-tenant-name"]
	policy = key_vault["b2c-sign-up-and-sign-in-user-flow"]
	post_logout_redirect_uri = url_for("index", _external=True)

	logout_url = (
		f"https://{tenant}.b2clogin.com/{tenant}.onmicrosoft.com/{policy}/oauth2/v2.0/logout"
		f"?post_logout_redirect_uri={post_logout_redirect_uri}"
	)
	return redirect(logout_url)


if __name__ == '__main__':
	# test blob upload - merge! 
	# try:
	# 	test_filename = r"C:\Users\panai\Downloads\CLOUD COMPUTING.pdf"
	# 	with open(test_filename, "w") as f:
	# 		f.write("Test upload")

	# 	upload_file_to_container(test_filename, test_filename)

	# 	print("upload test successful.")
	# except Exception as e:
	# 	print("upload test failed:", str(e))


	webbrowser.open('http://localhost:5000/events')
	app.run()
