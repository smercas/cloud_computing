import os

from dotenv import load_dotenv
from identity.flask import Auth
from flask import (	Flask, redirect, render_template, request,
					send_from_directory, url_for)

load_dotenv()

import azure.storage.blob
from azure.storage.blob import BlobServiceClient
import app_config

# AZURE_CONNECTION_STRING = " DefaultEndpointsProtocol=https;AccountName=calendarappstoragecloud;AccountKey=40Qtdu5nPtczY4yg8wbWcNVnLiYWBCkaDGbQ3fMvBQ1/lgkEuKRC9rV+/UM6utw38Jp8PjUTemsr+AStCeFD6g==;EndpointSuffix=core.windows.net" # DON'T PUT SECRETS HERE BTW; if you're sure abt using some secrets, you can add them to the env in github > setting > secrets and variables
# CONTAINER_NAME = "event-files"
# blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
# container_client = blob_service_client.get_container_client(CONTAINER_NAME)

def upload_file_to_container(file_name, file_path):
    # Upload the file to the container
    with open(file_path, "rb") as data:
        container_client.upload_blob(name=file_name, data=data)
        print(f"File {file_name} uploaded to container {CONTAINER_NAME}.")

app = Flask(__name__)
app.config.from_object(app_config)
auth = Auth(
	app,
	client_id=os.getenv("CLIENT_ID"),
	client_credential=os.environ["APP_REGISTRATION_CLIENT_SECRET"],
	redirect_uri=os.getenv("REDIRECT_URI"),
	b2c_tenant_name=os.getenv('B2C_TENANT_NAME'),
	b2c_signup_signin_user_flow=os.getenv('SIGNUP_SIGNIN_USER_FLOW'),
	b2c_edit_profile_user_flow=os.getenv('EDITPROFILE_USER_FLOW'),
	b2c_reset_password_user_flow=os.getenv('RESETPASSWORD_USER_FLOW'),
)



@app.route('/')
@auth.login_required
def index():
	print(request.headers)
	print('Request for index page received')
	return render_template('index.html')

@app.route('/redirect')
def redirect():
	print(request.headers)
	print('Request for redirect page received')
	return "balls"

@app.route('/favicon.ico')
def favicon():
	print(request.headers)
	return send_from_directory(os.path.join(app.root_path, 'static'),
							'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/hello', methods=['POST'])
def hello():
	print(request.headers)
	name = request.form.get('name')

	if name:
		print('Request for hello page received with name=%s' % name)
		return render_template('hello.html', name = name)
	else:
		print('Request for hello page received with no name or blank name -- redirecting')
	return redirect(url_for('index'))


if __name__ == '__main__':
	app.run()

# app.run(host='