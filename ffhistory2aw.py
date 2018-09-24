import sqlite3
from datetime import datetime


def main():
    conn = sqlite3.connect('/home/erb/.mozilla/firefox/32zlon1y.default/places.sqlite')
    conn.row_factory = sqlite3.Row
    query = \
        """SELECT moz_historyvisits.visit_date/1000000, moz_places.url, moz_places.title, moz_historyvisits.*
           FROM moz_places, moz_historyvisits
           WHERE moz_places.id = moz_historyvisits.place_id"""

    c = conn.execute(query)
    r = c.fetchall()
    print(conn.execute(query).fetchone().keys())
    print(r[0][3:])
    history = [{"timestamp": datetime.fromtimestamp(visit[0]), "data": {"title": visit[2], "url": visit[1]}} for visit in r]
    print(history[-1])

    # TODO: Send to bucket


if __name__ == "__main__":
    main()
