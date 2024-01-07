import socket
import sys
from datetime import datetime, time, timedelta

import gspread

import working_hours

td1d = timedelta(days=1)
break_time = 15 * 60


def update_sheet(regex, days_back):
    now = datetime.now().astimezone()
    today = (
        datetime.combine(now.date(), time()) + working_hours.day_offset
    ).astimezone()

    hostname = socket.gethostname()
    hostname_display = hostname.replace(".localdomain", "").replace(".local", "")

    # Create a list of time periods to query
    timeperiods = [(today - i * td1d, today - (i - 1) * td1d) for i in range(days_back)]
    timeperiods.reverse()

    # Run the query function from the original script and get the result
    res = working_hours.query(regex, timeperiods, hostname, save=False)

    # Use your own Google Sheets API credentials here
    gc = gspread.service_account()

    # Open the Google Sheet
    sh = gc.open_by_key("1YcPTwTqYf3ZPG6JAdADAp7orz5tKd4OT677rQG9dv-U")

    # worksheet = sh.add_worksheet(title="A worksheet", rows=100, cols=20)
    worksheet = sh.worksheet(f"worked-{hostname_display}")

    # Get the most recent entry from the Google Sheet
    values = worksheet.get_all_values()
    if values:
        last_row = values[-1]
        last_date = datetime.strptime(last_row[0], "%Y-%m-%d").date()
    else:
        last_date = None

    # Iterate over the result and update or append the data to the Google Sheet
    for tp, r in zip(timeperiods, res):
        date = tp[0].date()
        duration = (
            working_hours.generous_approx(r["events"], break_time).total_seconds()
            / 3600
        )
        row = [str(date), duration]

        # If the date is the same as the last entry, update it
        if last_date and date == last_date:
            print(f"Updating {row}")
            worksheet.update_cell(len(worksheet.get_all_values()), 2, duration)
        # If the date is later than the last entry, append it
        elif not last_date or date > last_date:
            print(f"Appending {row}")
            worksheet.append_row(row, value_input_option="USER_ENTERED")
        else:
            print(f"Skipping {row}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        regex = sys.argv[1]
    else:
        print("Usage: python3 working_hours_gspread.py [regex]")
        exit(1)

    update_sheet(regex, 40)
