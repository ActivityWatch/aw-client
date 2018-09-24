from datetime import datetime

from tabulate import tabulate


# Export done using https://benjaminbenben.com/lastfm-to-csv/
with open('/home/erb/annex/Logs/Lastfm to csv - ErikBjare.csv') as f:
    lines = [line.strip().split(",") for line in f.readlines()]
    #print(tabulate(lines[1:100], headers=lines[0]))
    events = [{"timestamp": datetime.strptime(track[3], '%d %b %Y %H:%M'), "data": {"artist": track[0], "album": track[1], "title": track[2]}} for track in lines]

    # TODO: Send to a bucket
