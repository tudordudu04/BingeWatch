import sqlite3;
import typer;
import json;
from typing_extensions import Annotated;
from typer import Argument;
from typer import Option;
from enum import Enum;
from urllib.parse import urlparse;
from urllib.request import urlopen, Request;
from html.parser import HTMLParser;
from urllib.error import HTTPError, URLError; #don't need rn
from datetime import date

app = typer.Typer(
    add_completion=False,
    context_settings={
        "help_option_names": ["-h", "--help"]
    }
)

conn = sqlite3.connect("bingewatcher.db")
conn.execute("PRAGMA foreign_keys = ON")
cursor = conn.cursor()

class Status(str, Enum):
    plan_to_watch = "plan_to_watch"
    watching = "watching"
    on_hold = "on_hold"
    dropped = "dropped"
    watched = "watched"

def init_db():
    schema = """CREATE TABLE IF NOT EXISTS shows(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'watching' NOT NULL
            CHECK (status IN ('plan_to_watch', 'watching', 'watched', 'dropped', 'on_hold')),
    latest_episode INTEGER DEFAULT 0 NOT NULL,
    last_watched INTEGER DEFAULT 0 NOT NULL,
    rating REAL DEFAULT 0 NOT NULL,
    imdb_link TEXT NOT NULL,
    notify INTEGER DEFAULT 1 NOT NULL
    );
                CREATE TABLE IF NOT EXISTS new_episodes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    plot TEXT,
    rating REAL DEFAULT 0 NOT NULL,
    has_trailer INTEGER DEFAULT 0 NOT NULL,
    trailer_link TEXT,
    FOREIGN KEY (show_id) REFERENCES shows(id) ON DELETE CASCADE
    );
    """
    cursor.executescript(schema)

try:
    init_db()
except sqlite3.Error as e:
    print("Initiation of database error: ", e)


def get_title_id(link: str) -> str:
    schema = urlparse(link)
    if schema.hostname != "www.imdb.com":
        return ""
    
    resource = schema.path.split("/")
    if resource[1] != "title":
        return ""
    
    title_id = resource[2]
    if not title_id:
        return ""
    
    if title_id[:2] != "tt" or not title_id[2:].isnumeric() or not len(title_id[2:]) >= 7:
        return ""
    return resource[2]

#irelevant
class ShowParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.is_show: bool
        self.in_title: bool = False
        self.in_head: bool = False

    def process(self, data: str):
        data = data[:data.find(")")]
        data = data[data.find("(")+1:].rstrip(" \t")
        self.is_show = False if data[0].isdigit() else True
        
    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True 
        elif tag == "title":
            self.in_title = True

    def handle_data(self, data):
        if self.in_title and self.in_head:
            self.process(data[0:-1])
        
    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "head":
            self.in_head = False
#irelevant
def parse_show(link: str) -> bool:
    request = Request(
        link,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0"},
        method="GET"
    )

    with urlopen(request) as response:
        html = response.read().decode("utf-8")
    
    parser = ShowParser()
    parser.feed(html)

    return parser.is_show  
#irelevant
def parse_episodes(link: str):
    request = Request(
        link,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0"},
        method="GET"
    )

    with urlopen(request) as response:
        html = response.read().decode("utf-8")

    parser = EpisodeParser()
    parser.feed(html)


def is_show(title_id: str) -> bool:
    url = f"https://api.imdbapi.dev/titles/{title_id}"

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0",
        },
        method="GET",
    )

    with urlopen(req) as response:
        body = json.load(response)

    # body = json.load(body)
    # print(json.loads(body))
    if body["type"] == "tvSeries":
        return True

    return False

def get_episodes(title_id: str) -> list:
    url = f"https://api.imdbapi.dev/titles/{title_id}/episodes"
    
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0"
        },
        method="GET",
    )

    with urlopen(req) as response:
        data = json.load(response)

    episode_list = []

    for episode in data["episodes"]:
        if "releaseDate" in episode.keys():
            release_date = date(episode["releaseDate"]["year"], episode["releaseDate"]["month"], episode["releaseDate"]["day"])
        else:
            break

        if release_date > date.today():
            break

        nr = episode["episodeNumber"]
        title = episode["title"]
        if "plot" in episode.keys():
            plot = episode["plot"]
        else:
            plot = ""
        if "rating" in episode.keys():
            rating = episode["rating"]["aggregateRating"]
        else:
            rating = 0

        episode_list.append({"nr": nr, "title": title, "plot": plot,  "rating": rating})

    return episode_list

def get_new_episodes(episode_list: list, show_id: int):
    command = """INSERT INTO new_episodes (show_id, number, title, plot, rating) VALUES (?,?,?,?,?)"""
    cursor.execute("SELECT last_watched FROM shows WHERE id = ?", (show_id,))
    last_watched = cursor.fetchone()[0]

    for episode in episode_list:
        if episode["nr"] > last_watched:
            cursor.execute(command, (show_id, episode["nr"], episode["title"], episode["plot"], episode["rating"]))
    
    conn.commit()

def delete_old_episodes(last_watched: int, show_id: int):
    cursor.execute("DELETE FROM new_episodes WHERE show_id = ? AND number <= ?", (show_id, last_watched))
    conn.commit()

#
@app.command(help = "Add tv shows into your local storage")
def add(
    name: Annotated[str, Argument(help = "Name of the show.")], 
    imdb_link: Annotated[str, Argument(help = "Link to the IMDb page for the show.")], 
    status: Annotated[Status, Argument(help = "Watching status of the show.")] = "watching", 
    last_watched: Annotated[int, Option("--last-watched", "-l", help = "Number of the last watched episode.")] = 0,
    rating: Annotated[float, Option("--rating", "-r", help = "Rating for the show between 1 and 10.")] = 0, 
    notify: Annotated[bool, Option(" /--notify", " /-n", help = "Flag for if you DON'T want to be notified of new content.")] = True
):    
    command = """INSERT INTO shows (title_id, name, imdb_link, status, latest_episode, last_watched, rating, notify) VALUES (?,?,?,?,?,?,?,?)"""
    
    title_id = get_title_id(imdb_link) 

    if title_id == "":
        raise typer.Exit("Invalid IMDb link for show.")

    if not is_show(title_id):
        raise typer.Exit("Not a show.")

    episode_list = get_episodes(title_id)

    try:
        cursor.execute(command, (title_id, name, imdb_link, status, len(episode_list), last_watched, rating, notify))
        cursor.execute("SELECT id FROM shows WHERE name = ?", (name,))
        show_id = cursor.fetchone()[0]
        conn.commit()
        if notify:
            get_new_episodes(episode_list, show_id)
        catalog()
    except sqlite3.Error as e:
        conn.rollback()
        raise typer.Exit(f"Error adding show: {e}")

#TODO Validate links and other stuff
@app.command(help = "Update information about shows")
def update(
    name: Annotated[str, Argument(help = "Name of show you want to update.")],
    new_name: Annotated[str, Option("--new-name", "-n", help = "Update name of show to new_name.")] = None,
    last_watched: Annotated[int, Option("--last-watched", "-l", help = "Update number of the last watched episode.")] = None,
    rating: Annotated[float, Option("--rating", "-r", help = "Update the rating of show.")] = None,
    notify: Annotated[int, Option("--notify", "-t", help = "Update notification status for show.")] = None,
    status: Annotated[Status, Option("--status", "-s", help = "Update watching status for show.")] = None
):

    updates = {}
    if new_name:
        updates["name"] = new_name
    if last_watched:
        updates["last_watched"] = str(last_watched)
    if rating:
        updates["rating"] = str(rating)
    if notify in (0,1):
        updates["notify"] = str(int(notify))
    elif status:
        if status.name == "plan_to_watch" or status.name == "watching":
            updates["notify"] = "1"
        else:
            updates["notify"] = "0"
    if status:
        updates["status"] = status.name

    if not updates:
        return

    # command = "UPDATE shows SET " + str.join(", ", list(map(lambda key: key + "='" + updates[key] + "'", updates.keys()))) + " WHERE name='" + name + "'"
    # set_clause = str.join(", ", (f"{col} = ?" for col in updates.keys()))

    cursor.execute("SELECT id FROM shows WHERE name = ?", (name,))
    show_id = cursor.fetchone()[0]

    set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
    command = f"UPDATE shows SET {set_clause} WHERE name = ?"

    params = (updates.values()) + [name]
    cursor.execute(command, params)
    conn.commit()
    
    if last_watched:
        delete_old_episodes(last_watched, show_id)

@app.command(help = "Delete one show from storage")
def delete(name: Annotated[str, Argument(help = "Name of show you want to delete.")]):
    # delete = typer.confirm(f"Are you sure you want to delete {name}?")
    if not delete:
        raise typer.Exit("Delete canceled.")
    command = "DELETE FROM shows WHERE name = ?"
    cursor.execute(command, (name,))
    
    conn.commit()
    print("Deleted succesfully.")


@app.command(help = "Print out information")
def catalog():
    command = """SELECT * FROM shows"""
    try:
        cursor.execute(command)
        print(cursor.fetchall())
        cursor.execute("SELECT * from new_episodes")
        for ep in cursor.fetchall(): 
            print(ep)
        # delete("Pluribus")
    except sqlite3.Error as e:
        print("List command fail: ", e)
        conn.rollback()

@app.command(help = "Flips the notify flag for a show.")
def notify(name: Annotated[str, Argument(help = "Name of the show you want to change the notify flag for.")]):
    cursor.execute("SELECT notify FROM shows WHERE name = ?", (name,))
    notify = cursor.fetchone()[0]
    notify = 0 if notify else 1

    cursor.execute("UPDATE shows SET notify = ? WHERE name = ?", (notify, name))
    conn.commit()

# @app.command("list", help = "Command for listing shows or new episodes")
# def list_cmd():
    

app()