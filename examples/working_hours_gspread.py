"""
This script uses ActivityWatch events to updates a Google Sheet
with the any events matching a regex for the last `days_back` days.

It uses the `working_hours.py` example to calculate the working hours
and the `gspread` library to interact with Google Sheets.

The Google Sheet is identified by its key, which is hardcoded in the script.
The script uses a service account for authentication with the Google Sheets API.

The script assumes that the Google Sheet has a worksheet for each hostname, named "worked-{hostname}".
If such a worksheet does not exist, the script will fail.

The working hours are calculated generously, meaning that if the time between two consecutive
events is less than `break_time` (10 minutes by default), it is considered as working time.

Usage:
    python3 working_hours_gspread.py <sheet_key> <regex>
"""
import socket
import sys
from datetime import datetime, time, timedelta

import gspread

import working_hours

td1d = timedelta(days=1)
break_time = 10 * 60


def update_sheet(sheet_key: str, regex: str):
    """
    Update the Google Sheet with the working hours for the last `days_back` days.

    1. Open the sheet and get the last entry
    2. Query the working hours for the days since the last entry
    3. Update the last entry in the Google Sheet (if any)
    4. Append any new entries
    """

    hostname = socket.gethostname()
    hostname_display = hostname.replace(".localdomain", "").replace(".local", "")

    try:
        gc = gspread.service_account()
    except Exception as e:
        print(e)
        print(
            "Failed to authenticate with Google Sheets API.\n"
            "Make sure you have a service account key in ~/.config/gspread/service_account.json\n"
            "See https://gspread.readthedocs.io/en/latest/oauth2.html#for-bots-using-service-account"
        )
        exit(1)

    # Open the sheet
    sh = gc.open_by_key(sheet_key)
    print(f"Updating document: {sh.title}")
    worksheet = sh.worksheet(f"worked-{hostname_display}")
    print(f"Updating worksheet: {worksheet.title}")

    # Get the most recent entry from the Google Sheet
    values = worksheet.get_all_values()
    if values:
        last_row = values[-1]
        last_date = datetime.strptime(last_row[0], "%Y-%m-%d").date()
    else:
        last_date = None

    last_datetime = (
        (datetime.combine(last_date, time()) + working_hours.day_offset).astimezone()
        if last_date
        else None
    )

    if last_datetime:
        print(f"Last entry: {last_datetime}")

    now = datetime.now().astimezone()
    today = (
        datetime.combine(now.date(), time()) + working_hours.day_offset
    ).astimezone()

    # Create a list of time periods to query, from last_date or days_back_on_new back if None
    days_back_on_new = 30
    days_back = (today - last_datetime).days + 1 if last_datetime else days_back_on_new
    timeperiods = [(today - i * td1d, today - (i - 1) * td1d) for i in range(days_back)]
    timeperiods.reverse()

    # Run the query function from the original script and get the result
    res = working_hours.query(regex, timeperiods, hostname)

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
    if len(sys.argv) == 3:
        sheet_key = sys.argv[1]
        regex = sys.argv[2]
    else:
        print("Usage: python3 working_hours_gspread.py <sheet_key> <regex>")
        exit(1)

    update_sheet(sheet_key, regex)
