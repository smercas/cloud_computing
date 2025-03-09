from contextlib import contextmanager
import functools
import operator
import re
import sqlite3
import json
import random
import string
from http.server import BaseHTTPRequestHandler, HTTPServer
import validators

PATH_TO_DB_FILE = "hw1_db.db"
MAIN_DB_TABLE_NAME = "main"

class dbContextManager:
	def __init__(self, writes: bool=False):
		self.writes = writes
	@classmethod
	def for_writes(cls): return cls(True)
	@classmethod
	def for_reads(cls): return cls()
	def __enter__(self):
		self.connection = sqlite3.connect(PATH_TO_DB_FILE)
		return self.connection.cursor()
	def __exit__(self, exc_type, exc_value, exc_tb):
		if self.writes: self.connection.commit()
		self.connection.close()

def init_db() -> None:
	with dbContextManager.for_writes() as cursor:
		cursor.execute(f"""
			CREATE TABLE IF NOT EXISTS {MAIN_DB_TABLE_NAME} (
				alias TEXT PRIMARY KEY,
				url TEXT NOT NULL
			)
		""")

valid_alias_regex = r"^[a-zA-Z0-9_]+$"
def is_valid_alias(s: str) -> bool: return re.match(valid_alias_regex, s)
def generate_alias(length=6) -> None:
	return functools.reduce(operator.add, random.choices(string.ascii_letters + string.digits + '_', k=length))

def is_valid_url(s: str) -> bool: return validators.url(s) == True

class URLAliasingHandler(BaseHTTPRequestHandler):
	def __send_container_as_json(self, code: int, to_send):
		self.send_response(code)
		self.send_header("Content-Type", "application/json")
		self.end_headers()
		self.wfile.write(json.dumps(to_send).encode())
	def __send_invalid_alias_error(self, code: int=400):
		self.send_error(code, "Invalid alias", "provided aliases can only contain letters, digits and underscores")
	def __send_invalid_url_error(self, code: int=400):
		self.send_error(code, "Invalid URL")

	def do_POST(self):
		path_parts = self.path.strip("/").split("/")

		if len(path_parts) == 1 and path_parts[0] == "alias":
			content_length = int(self.headers['Content-Length'])
			post_data = self.rfile.read(content_length)
			
			try:
				data = json.loads(post_data)
			except:
				self.send_error(400, "Invalid JSON")
				return
			alias = data.get("alias")
			url = data.get("url")

			if url is None:
				self.send_error(400, "Missing 'url' field")
				return
			if not is_valid_url(url):
				self.__send_invalid_url_error()
				return
			if alias is not None and not is_valid_alias(alias):
				self.__send_invalid_alias_error()
				return

			with dbContextManager.for_writes() as cursor:
				if alias is not None:
					cursor.execute(f"SELECT * FROM {MAIN_DB_TABLE_NAME} WHERE alias=?", (alias,))
					if cursor.fetchone() is not None:
						self.send_error(400, "Alias already taken")
						return
				else:
					while True:
						alias = generate_alias()
						cursor.execute(f"SELECT * FROM {MAIN_DB_TABLE_NAME} WHERE alias=?", (alias,))
						if cursor.fetchone() is None: break

				cursor.execute(f"INSERT INTO {MAIN_DB_TABLE_NAME} (alias, url) VALUES (?, ?)", (alias, url))

			self.__send_container_as_json(201, {
				"message": "Alias created successfully",
				"alias": f"http://localhost/{alias}"
			})
		else:
			self.send_error(400, "Invalid request", "only acceptable requests are: alias")

	def do_PUT(self):
		path_parts = self.path.strip("/").split("/")

		if len(path_parts) == 2 and path_parts[0] == "alias":
			alias = path_parts[1]
			content_length = int(self.headers['Content-Length'])
			put_data = self.rfile.read(content_length)

			try:
				data = json.loads(put_data)
			except:
				self.send_error(400, "Invalid JSON")
				return
			new_url = data.get("new_url")

			if new_url is None:
				self.send_error(400, "Missing 'new_url' field")
				return
			if not is_valid_url(new_url):
				self.__send_invalid_url_error()
				return
			if not is_valid_alias(alias):
				self.__send_invalid_alias_error()
				return

			with dbContextManager.for_writes() as cursor:
				cursor.execute(f"SELECT * FROM {MAIN_DB_TABLE_NAME} WHERE alias=?", (alias,))
				if cursor.fetchone() is None:
					self.send_error(404, "Alias not found")
					return
				cursor.execute(f"UPDATE {MAIN_DB_TABLE_NAME} SET url=? WHERE alias=?", (new_url, alias))

			self.__send_container_as_json(200, {
				"message": "Alias updated successfully",
				"alias": alias,
				"new_url": new_url
			})
		else:
			self.send_error(400, "Invalid request", "only acceptable requests are: alias/<specific-alias>")

	def do_GET(self):
		path_parts = self.path.strip("/").split("/")

		if len(path_parts) == 2 and path_parts[0] == "alias" and path_parts[1] == "of_url":
			content_length = int(self.headers['Content-Length'])
			get_data = self.rfile.read(content_length)
			
			try:
				data = json.loads(get_data)
			except:
				self.send_error(400, "Invalid JSON")
				return
			url = data.get("url")

			if url is None:
				self.send_error(400, "Missing 'url' field")
				return
			if not is_valid_url(url):
				self.__send_invalid_url_error()
				return

			with dbContextManager.for_reads() as cursor:
				cursor.execute(f"SELECT alias FROM {MAIN_DB_TABLE_NAME} WHERE url=?", (url,))
				results = cursor.fetchall()

			aliases = [row[0] for row in results]
			self.__send_container_as_json(200, aliases)
		elif len(path_parts) == 2 and path_parts[0] == "alias":
			alias = path_parts[1]
			if not is_valid_alias(alias):
				self.__send_invalid_alias_error()
				return

			with dbContextManager.for_reads() as cursor:
				cursor.execute(f"SELECT url FROM {MAIN_DB_TABLE_NAME} WHERE alias=?", (alias,))
				result = cursor.fetchone()
			if result is None:
				self.send_error(404, "Alias not found")
				return

			self.__send_container_as_json(200, result[0])
		elif len(path_parts) == 1 and path_parts[0] == "alias":
			with dbContextManager.for_reads() as cursor:
				cursor.execute(f"SELECT alias FROM {MAIN_DB_TABLE_NAME}")
				results = cursor.fetchall()

			aliases = [row[0] for row in results]
			self.__send_container_as_json(200, aliases)
		else:
			self.send_error(400, "Invalid request", "only acceptable requests are: alias/<specific-alias>, alias/of_url/<specific-url> and alias")

	def do_DELETE(self):
		path_parts = self.path.strip("/").split("/")

		if len(path_parts) == 2 and path_parts[0] == "alias":
			alias = path_parts[1]
			if not is_valid_alias(alias):
				self.__send_invalid_alias_error()
				return

			with dbContextManager.for_writes() as cursor:
				cursor.execute(f"DELETE FROM {MAIN_DB_TABLE_NAME} WHERE alias=?", (alias,))
				deleted = cursor.rowcount

			if deleted == 0:
				self.send_error(404, "Alias not found")
				return
			self.__send_container_as_json(200, "Alias deleted successfully")
		else:
			self.send_error(400, "Invalid request", "only acceptable requests are: alias/<specific-alias>")

def run(server_class=HTTPServer, handler_class=URLAliasingHandler, port=5000):
	init_db()
	server_address = ('', port)
	httpd = server_class(server_address, handler_class)
	print(f"Starting server on port {port}")
	httpd.serve_forever()

if __name__ == "__main__":
	run()
