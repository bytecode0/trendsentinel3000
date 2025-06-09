from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime
from bot.btc_bot import trading_job

def start_scheduler():
    scheduler = BlockingScheduler()
    scheduler.add_job(trading_job, 'interval', hours=4, next_run_time=datetime.now())
    scheduler.start()