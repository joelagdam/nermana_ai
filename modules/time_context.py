from datetime import datetime

def get_time_context():
    return datetime.now().strftime("Current local time: %A, %B %d, %Y, %I:%M %p")

def get_time_short():
    return datetime.now().strftime("%I:%M %p on %A, %B %d, %Y")
