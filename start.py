from app import run_admin_panel
from bot.jobs import start_scheduler
from threading import Thread

def run_bot_scheduler():
    start_scheduler()

if __name__ == "__main__":
    # Lanza el bot como hilo en segundo plano
    Thread(target=start_scheduler, daemon=True).start()

    # Lanza el panel admin Flask
    run_admin_panel()