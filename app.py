from datetime import datetime, timedelta
from functools import wraps
import os

from dotenv import load_dotenv
load_dotenv()

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.automap import automap_base, AutomapBase
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, MetaData, Table, event
from identity.flask import Auth
from flask import (	Flask, redirect, render_template, request,
										send_from_directory, url_for)

from backend import utils

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from backend.key_vault import KeyVault
key_vault = KeyVault(default_transform=utils.to_value)

credential = DefaultAzureCredential()
blob_service_client = BlobServiceClient(account_url=key_vault["blob-account-url"], credential=credential)
container_client = blob_service_client.get_container_client(key_vault["blob-container-name"])

app = Flask(__name__)
app.config.from_object('app_config')
app.config["SQLALCHEMY_DATABASE_URI"] = key_vault["database-uri"]
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
base: AutomapBase = automap_base()
with app.app_context():
	base.prepare(db.engine)

users = base.classes.users
events = base.classes.events
notes = base.classes.notes
file_attachments = base.classes.file_attachments
reminders = base.classes.reminders

events.field_transform_pairs = {
	'title':				utils.identity,
	'start_date':		datetime.fromisoformat,
	'end_date':			datetime.fromisoformat,
	'location':			utils.identity,
	'description':	utils.identity,
} # im too lazy to do reflection on the object and figure out a cleaner way of doing this

notes.field_transform_pairs = {
	"content":	utils.identity,
}

file_attachments.field_transform_pairs = {
	"file_name":		utils.identity,
	"file_size":		utils.identity,
	"content_type":	utils.identity,
	"blob_path":    utils.identity,
}

reminders.field_transform_pairs = {
	"seconds_before_notify":	utils.identity,
	"notify_by_email":				utils.identity,
	"notify_by_popup":				utils.identity,
}

def apply_data_to_automap(o, data: dict[str, str]):
	for field, transform in o.__class__.field_transform_pairs.items():
		v = data.get(field, None)
		if v is None: continue
		setattr(o, field, transform(v))

events.apply = apply_data_to_automap
notes.apply = apply_data_to_automap
file_attachments.apply = apply_data_to_automap
reminders.apply = apply_data_to_automap

# def automap_to_dict(o):
# 	res = {f: getattr(o, f) for f in o.__class__.field_transform_pairs.keys()}
# 	res["id"] = o.id
# 	return {k: v for k, v in res.items() if v is not None}
def automap_to_dict(o):
    res = {}
    for f in o.__class__.field_transform_pairs.keys():
        value = getattr(o, f)
        if value is not None:
            if isinstance(value, datetime):
                res[f] = value.isoformat()
            else:
                res[f] = value
    res["id"] = o.id
    return {k: v for k, v in res.items() if v is not None}

events.to_dict = automap_to_dict
notes.to_dict = automap_to_dict
file_attachments.to_dict = automap_to_dict
reminders.to_dict = automap_to_dict
users.to_dict = lambda u: {f: getattr(u, f) for f in ["id", "display_name", "email"]}

# region reminder processing

from sendgrid import Mail, SendGridAPIClient
def process_reminder(reminder, event, user):
	if reminder["notify_by_email"]:
		msg = Mail(
			from_email=		"smercas@gmail.com",
			to_emails=		user["email"],
			subject=			"fuck u",
			html_content=	f"""
				<html>
					<body>
						<h1>Reminder</h1>
						<p><strong>Message:</strong> fuck u again</p>
						<p><strong>Reminder:</strong> {str(reminder)}</p>
						<p><strong>Event:</strong> {str(event)}</p>
						<p><strong>User:</strong> {str(user)}</p>
					</body>
				</html>
			"""
		)
		try:
			sg = SendGridAPIClient(key_vault["sendgrid-api-key"])
			response = sg.send(msg)
			# print(f"Status code: {response.status_code}")
		except Exception as e:
			print(f"Error: {e}")
	if reminder["notify_by_popup"]:
	    print(f"Popup reminder triggered: {event['title']} for {user['email']} at {event['start_date']}")

with app.app_context():
	from backend.reminder_scheduler import ReminderScheduler
	scheduler = ReminderScheduler(db, reminders, events, users, process_reminder)
	scheduler.start()

# endregion reminder processing

class CustomAuth(Auth): # there might be issues here but i'm too dumb to figure them out
	@staticmethod
	def __add_user_if_needed(user_dict):
		session = Session(db.engine)
		user_entry = session.get(users, user_dict["oid"])
		if user_entry is not None: return
		user_entry = users(
			id=						user_dict["oid"],
			email=				user_dict["emails"][0],
			display_name=	user_dict["name"],
		)
		session.add(user_entry)
		session.commit()

	def login_required(self, function=None, /, *, scopes: list[str] | None=None):
		@wraps(function)
		def wrapper(*args, **kwargs):
			user = self._auth.get_user()
			context = self._login_required(self._auth, user, scopes)
			if context:
				self.__add_user_if_needed(user) # this line is the only change to the original implementation
				return function(*args, context=context, **kwargs)
			# Save an http 302 by calling self.login(request) instead of redirect(self.login)
			return self.login(next_link=self._request.url, scopes=scopes)
		return wrapper

auth = CustomAuth(
	app,
	client_id=										key_vault["app-registration-client-id"],
	client_credential=						key_vault["app-registration-client-secret"],
	redirect_uri=									os.environ["redirect_uri"],
	b2c_tenant_name=							key_vault["b2c-tenant-name"],
	b2c_signup_signin_user_flow=	key_vault['b2c-sign-up-and-sign-in-user-flow'],
	b2c_edit_profile_user_flow=		key_vault['b2c-edit-profile-user-flow'],
	b2c_reset_password_user_flow=	key_vault['b2c-reset-password-user-flow'],
)

# region events

@app.route("/events", methods=["GET"])
@auth.login_required
def get_events(*, context):
	session = Session(db.engine)
	scope = request.args.get("scope", None)  # day, week, month, year
	base_date_str = request.args.get("date", None)

	if all(v is not None for v in [scope, base_date_str]):
		try:
			base_date = datetime.fromisoformat(base_date_str)
		except ValueError:
			return {"error": "Invalid date format, use YYYY-MM-DD"}, 400

		match scope:
			case "day":
				start = base_date
				end = base_date + timedelta(days=1)
			case "week":
				start = base_date - timedelta(days=base_date.weekday())
				end = start + timedelta(weeks=1)
			case "month":
				start = base_date.replace(day=1)
				next_month = start.replace(day=28) + timedelta(days=4)
				end = next_month.replace(day=1)
			case "year":
				start = base_date.replace(month=1, day=1)
				end = start.replace(year=start.year + 1)
			case _:
				return {"error": "Unsupported scope"}, 400

		es = session.query(events).filter(
			events.user_id == context["user"]["oid"],
			events.start_date >= start,
			events.start_date < end
		).all()
		# es += session.query(events).filter(
		# 	events.user_id == context["user"]["oid"],
		# 	events.end_time >= start,
		# 	events.end_time < end
		# ).all()
	else:
		es = session.query(events).filter(events.user_id == context["user"]["oid"]).all()

	return {"events": list(map(automap_to_dict, es))}

@app.route("/events", methods=["POST"])
@auth.login_required
# def create_event(*, context):
# 	data = request.json
# 	session = Session(db.engine)
# 	event = events(
# 		title=				data['title'],
# 		start_time=		datetime.fromisoformat(data['start_time']),
# 		end_time=			datetime.fromisoformat(data['end_time']),
# 		location=			data.get('location', None),
# 		description=	data.get('description', None),
# 		user_id=			context["user"]["oid"],
# 	)
# 	session.add(event)
# 	session.commit()
# 	return automap_to_dict(event), 201

def create_event(*, context):
    try:
        data = request.json
        print(f"Received data: {data}")  
        
        session = Session(db.engine)
        
        required_fields = ['title', 'start_date', 'end_date']
        for field in required_fields:
            if field not in data:
                return {"error": f"Missing required field: {field}"}, 400
        
        try:
            start_date = datetime.fromisoformat(data['start_date'])
            end_date = datetime.fromisoformat(data['end_date'])
        except ValueError as e:
            return {"error": f"Invalid date format: {str(e)}"}, 400
        
        print(f"Events class attributes: {dir(events)}")
        
        event = events()
        event.title = data['title']
        event.start_date = start_date
        event.end_date = end_date
        event.location = data.get('location', None)
        event.description = data.get('description', None)
        event.user_id = context["user"]["oid"]
        
        print(f"Created event object: {event}")  # Debug print
        print(f"Event start_date: {event.start_date}")
        print(f"Event end_date: {event.end_date}")
        
        session.add(event)
        session.commit()
        
        return automap_to_dict(event), 201
	
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error creating event: {str(e)}")
        print(f"Full traceback: {error_details}")
        
        return {
            "error": "Failed to create event",
            "details": str(e),
            "traceback": error_details
        }, 500

@app.route("/events/<event_id>", methods=["GET"])
@auth.login_required
def get_event(event_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	return automap_to_dict(event), 200

@app.route("/events/<event_id>", methods=["PUT"])
@auth.login_required
def update_event(event_id, *, context):
	data = request.json
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
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
	if event is None:
		return {"error": "Event not found"}, 404
	session.delete(event)
	session.commit()
	return {}, 204

# endregion events

# region notes

@app.route("/events/<event_id>/notes", methods=["GET"])
@auth.login_required
def get_notes(event_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	ns = session.query(notes).filter_by(event_id=event_id).all()
	return {"notes": list(map(automap_to_dict, ns))}, 200

@app.route("/events/<event_id>/notes", methods=["POST"])
@auth.login_required
def add_note(event_id, *, context):
	data = request.json
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	note = notes(
		event_id=	event_id,
		content=	data['content'],
	)
	session.add(note)
	session.commit()
	return automap_to_dict(note), 201

@app.route("/events/<event_id>/notes/<note_id>", methods=["PUT"])
@auth.login_required
def update_note(event_id, note_id, *, context):
	data = request.json
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	note = session.get(notes, note_id)
	if note is None or note.event_id != event_id:
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
	if event is None:
		return {"error": "Event not found"}, 404
	note = session.get(notes, note_id)
	if note is None or note.event_id != event_id:
		return {"error": "Note not found"}, 404
	session.delete(note)
	session.commit()
	return {}, 204

# endregion notes

# region file attachments

@app.route("/events/<event_id>/upload", methods=["POST"])
@auth.login_required
# def upload_file(event_id, *, context):
# 	session = Session(db.engine)
# 	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
# 	if event is None:
# 		return {"error": "Event not found"}, 404
	
# 	upload = request.files.get('file', None)
# 	if upload is None:
# 		return {"error": "No file part in the request"}, 400

# 	if upload.filename == '':
# 		return {"error": "No selected file"}, 400

# 	file_data = upload.read()
# 	file_entry = file_attachments(
# 		event_id=			event_id,
# 		file_name=		upload.filename,
# 		file_size=		len(file_data),
# 		content_type=	upload.content_type,
# 	)
# 	session.add(file_entry)
# 	session.commit()

# 	filename = f"{event_id}_{file_entry.id}_{upload.filename}"
# 	container_client.upload_blob(name=filename, data=file_data)

# 	file_entry.blob_name = filename
# 	session.commit()
# 	return automap_to_dict(file_entry), 201

def upload_file(event_id, *, context):
    try:
        session = Session(db.engine)
        event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
        if event is None:
            return {"error": "Event not found"}, 404
        
        upload = request.files.get('file', None)
        if upload is None:
            return {"error": "No file part in the request"}, 400

        if upload.filename == '':
            return {"error": "No selected file"}, 400

        print(f"Uploading file: {upload.filename}")  # Debug
        
        file_data = upload.read()
        import time
        temp_id = int(time.time() * 1000) 
        blob_filename = f"{event_id}_{temp_id}_{upload.filename}"
        
        file_entry = file_attachments(
            event_id=event_id,
            file_name=upload.filename,
            file_size=len(file_data),
            content_type=upload.content_type,
            blob_path=blob_filename,  
        )
        session.add(file_entry)
        session.commit()
        
        try:
            blob_client = container_client.get_blob_client(blob_filename)
            blob_client.upload_blob(file_data, overwrite=True)
            print(f"File uploaded to blob storage successfully")  # Debug
        except Exception as blob_error:
            print(f"Blob upload error: {str(blob_error)}")  # Debug
            session.delete(file_entry)
            session.commit()
            raise blob_error
        
        session.commit()
        
        return automap_to_dict(file_entry), 201
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Error uploading file: {str(e)}")
        print(f"Full traceback: {error_details}")
        return {
            "error": "Failed to upload file",
            "details": str(e)
        }, 500

@app.route("/events/<event_id>/files", methods=["GET"])
@auth.login_required
def list_files(event_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	files = session.query(file_attachments).filter_by(event_id=event_id).all()
	return {"files": [automap_to_dict(f) for f in files]}, 200

@app.route("/events/<event_id>/files/<file_id>", methods=["GET"])
@auth.login_required
# def get_file_contents(event_id, file_id, *, context):
# 	session = Session(db.engine)
# 	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
# 	if not event:
# 		return {"error": "Event not found"}, 403

# 	file = session.get(file_attachments, file_id)
# 	if file is None or file.event_id != event_id:
# 		return {"error": "File not found"}, 404

# 	try:
# 		blob = container_client.download_blob(file.blob_path)
# 		body = blob.readall()
# 		headers = {
# 			"Content-Type": file.content_type or "application/octet-stream",
# 			"Content-Disposition": f"attachment; filename={file.file_name}"
# 		}
# 		return body, 200, headers
# 	except Exception as e:
# 		return {"error": f"Failed to fetch blob: {str(e)}"}, 500
@app.route("/events/<event_id>/files/<file_id>", methods=["GET"])
@auth.login_required
def get_file_contents(event_id, file_id, *, context):
    session = Session(db.engine)
    try:
        # Convertește ID-urile la integer
        event_id = int(event_id)
        file_id = int(file_id)
        
        event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
        if not event:
            return {"error": "Event not found"}, 403

        file = session.get(file_attachments, file_id)
        if file is None or file.event_id != event_id:
            return {"error": "File not found"}, 404
            
        try:
            blob = container_client.download_blob(file.blob_path)
            body = blob.readall()
            headers = {
                "Content-Type": file.content_type or "application/octet-stream",
                "Content-Disposition": f"attachment; filename={file.file_name}"
            }
            return body, 200, headers
        except Exception as e:
            print(f"Error downloading blob: {str(e)}")
            return {"error": f"Failed to fetch blob: {str(e)}"}, 500
    finally:
        session.close()
	

@app.route("/events/<event_id>/files/<file_id>", methods=["DELETE"])
@auth.login_required
# def delete_file(event_id, file_id, *, context):
# 	session = Session(db.engine)
# 	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
# 	if event is None:
# 		return {"error": "Event not found"}, 404

# 	file = session.get(file_attachments, file_id)
# 	if file is None or file.event_id != event_id:
# 		return {"error": "File not found"}, 404

# 	try:
# 		container_client.delete_blob(file.blob_name)
# 	except Exception as e:
# 		print(f"Warning: Failed to delete blob {file.blob_name}: {e}")

# 	session.delete(file)
# 	session.commit()
# 	return {}, 204

def delete_file(event_id, file_id, *, context):
    session = Session(db.engine)
    event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
    if event is None:
        return {"error": "Event not found"}, 404

    file = session.get(file_attachments, file_id)
    if file is None or file.event_id != event_id:
        return {"error": "File not found"}, 404

    try:
        container_client.delete_blob(file.blob_path)  
    except Exception as e:
        print(f"Warning: Failed to delete blob {file.blob_path}: {e}")

    session.delete(file)
    session.commit()
    return {}, 204

# endregion file attachments

# region reminders

@app.route("/events/<event_id>/reminders", methods=["GET"])
@auth.login_required
def get_reminders(event_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	rs = session.query(reminders).filter_by(event_id=event_id).all()
	return {"reminders": list(map(automap_to_dict, rs))}, 200

@app.route("/events/<event_id>/reminders", methods=["POST"])
@auth.login_required
def add_reminder(event_id, *, context):
    data = request.json
    session = Session(db.engine)
    event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
    if event is None:
        return {"error": "Event not found"}, 404
    
    reminder = reminders(
        event_id=event_id,
        seconds_before_notify=data.get('seconds_before_notify', 43200),  # default 12 ore
        notify_by_email=data.get('notify_by_email', False),
        notify_by_popup=data.get('notify_by_popup', True)
    )
    session.add(reminder)
    session.commit()
    
    scheduler.add(reminder)
    
    return automap_to_dict(reminder), 201

@app.route("/events/<event_id>/reminders/<reminder_id>", methods=["PUT"])
@auth.login_required
def update_reminder(event_id, reminder_id, *, context):
	data = request.json
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	reminder = session.get(reminders, reminder_id)
	if reminder is None or reminder.event_id != event_id:
		return {"error": "reminder not found"}, 404
	apply_data_to_automap(reminder, data)
	# reminder.apply(data) #TODO: see if this works
	scheduler.add(reminder)
	session.commit()
	return {}, 200

@app.route("/events/<event_id>/reminders/<reminder_id>", methods=["DELETE"])
@auth.login_required
def delete_reminder(event_id, reminder_id, *, context):
	session = Session(db.engine)
	event = session.query(events).filter_by(id=event_id, user_id=context["user"]["oid"]).first()
	if event is None:
		return {"error": "Event not found"}, 404
	reminder = session.get(reminders, reminder_id)
	if reminder is None or reminder.event_id != event_id:
		return {"error": "reminder not found"}, 404
	session.delete(reminder)
	scheduler.remove(reminder)
	session.commit()
	return {}, 204

#endregion reminders


@app.route("/homepage", methods=["GET"])
def homepage():
    return render_template('homepage.html')

@app.route("/login")
def login_redirect():
    return auth.login(next_link=url_for('calendar'))

@app.route("/signup")
def signup_redirect():
    return auth.login(
        next_link=url_for('calendar'),
        scopes=[]  
    )


@app.route("/calendar", methods=["GET"])
@auth.login_required
def calendar(*, context):
    #return "route is working"
    return render_template('calendar.html')

@app.route('/')
@auth.login_required
def index(*, context):
	return redirect(url_for('calendar'))

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

@app.route("/notifications")
@auth.login_required
def get_notifications(*, context):
    from datetime import datetime
    session = Session(db.engine)
    try:
        now = datetime.utcnow()
        user_reminders = session.query(reminders).join(events).filter(
            events.user_id == context["user"]["oid"],
            reminders.notify_by_popup == True
        ).all()
        
        notifications = []
        for r in user_reminders:
            event = session.get(events, r.event_id)
            if event:
                notification_time = event.start_date - timedelta(seconds=r.seconds_before_notify)
                
                if now >= notification_time and now <= notification_time + timedelta(minutes=5):
                    notifications.append({
                        "title": f"Upcoming Event: {event.title}",
                        "body": f"{event.description or ''} starts at {event.start_date.strftime('%H:%M')}"
                    })
        
        return {"notifications": notifications}
    finally:
        session.close()


if __name__ == '__main__':
	# for rule in app.url_map.iter_rules():
	# 	print(f"{rule.endpoint:30s} {','.join(rule.methods):20s} {rule}")
	print(key_vault["b2c-sign-up-and-sign-in-user-flow"])

	app.run()
