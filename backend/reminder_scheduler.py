from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

class ReminderScheduler:
	def __init__(self, db: SQLAlchemy, events, reminders, process_fn):
		self.__db = db
		self.__events = events
		self.__reminders = reminders
		self.__process_fn = process_fn
		self.__scheduler = BackgroundScheduler(jobstores={
			'default': SQLAlchemyJobStore(engine=self.__db.engine)
		})

	def start(self):
		self.__schedule_all_reminders()
		self.__scheduler.start()

	def __schedule_all_reminders(self):
		session = Session(self.__db.engine)

		rs = session.query(self.__reminders)\
								.join(self.__events, self.__events.id == self.__reminders.event_id)\
								.filter(func.DATEADD(
									text("SECOND"),
									-self.__reminders.seconds_before_notify,
									self.__events.start_date,
								) > datetime.now())\
								.all()

		for r in rs:
			self.add(r)

		session.close()
	
	def add(self, reminder):
		session = Session(self.__db.engine)
		event = session.get(self.__events, reminder.event_id)
		notify_date = event.start_date - timedelta(seconds=reminder.seconds_before_notify)
		self.__scheduler.add_job(
			self.__process_fn,
			'date',
			run_date=notify_date,
			args=[reminder.to_dict(), event.to_dict()],
			id=str(reminder.id),
			replace_existing=True
		)

	def remove(self, reminder):
		self.__scheduler.remove_job(reminder.id, "default")
