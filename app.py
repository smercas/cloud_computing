import os

from dotenv import load_dotenv
load_dotenv()
from identity.flask import Auth
from flask import (	Flask, redirect, render_template, request,
										send_from_directory, url_for)

from backend import utils

import azure.storage.blob
from azure.storage.blob import BlobServiceClient

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
import app_config
app.config.from_object(app_config)

from backend.key_vault import KeyVault
key_vault = KeyVault(default_transform=utils.to_value)

auth = Auth(
	app,
	client_id=										key_vault["app-registration-client-id"],
	client_credential=						key_vault["app-registration-client-secret"],
	redirect_uri=									os.environ["redirect-uri"],
	b2c_tenant_name=							key_vault["b2c-tenant-name"],
	b2c_signup_signin_user_flow=	key_vault['b2c-sign-up-and-sign-in-user-flow'],
	b2c_edit_profile_user_flow=		key_vault['b2c-edit-profile-user-flow'],
	b2c_reset_password_user_flow=	key_vault['b2c-reset-password-user-flow'],
)



@app.route('/')
@auth.login_required
def index(*, context):
	print('Request for index page received')
	return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
	return send_from_directory(os.path.join(app.root_path, 'static'),
							'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/hello', methods=['POST'])
def hello():
	name = request.form.get('name')

	if name:
		print(f'Request for hello page received with name={name}')
		return render_template('hello.html', name = name)
	print('Request for hello page received with no name or blank name -- redirecting')
	return redirect(url_for('index'))


if __name__ == '__main__':
	app.run()
