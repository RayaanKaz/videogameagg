import streamlit as st
import requests
import json
import sqlite3
import google.generativeai as genai
import os
from dotenv import load_dotenv
import hashlib
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from datetime import datetime

load_dotenv()

# Steam API Key
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

# Configure Google Generative AI
GENAI_API_KEY = os.getenv("GENAI_API_KEY")
import google.generativeai as genai
genai.configure(api_key=GENAI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# IGDB API Credentials
CLIENT_ID = os.getenv("IGDB_CLIENT_ID")
ACCESS_TOKEN = os.getenv("IGDB_ACCESS_TOKEN")
BASE_URL = "https://api.igdb.com/v4"

# Database setup
DB_FILE = "steam_games_recommendations.db"

# Initialize database
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY,
            steam_game_id TEXT,
            game_name TEXT,
            playtime INTEGER,
            genres TEXT,
            cover_url TEXT,
            store_url TEXT,
            added_on TIMESTAMP,
            user_id INTEGER,
            steam_user_id TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            game_id INTEGER,
            review_text TEXT,
            rating INTEGER CHECK(rating >= 1 AND rating <= 5),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(game_id) REFERENCES games(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_user_id TEXT,
            user_id INTEGER,
            label TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            UNIQUE(steam_user_id, user_id)
        );
    """)

    # Create wishlist table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wishlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            steam_game_id TEXT NOT NULL,
            game_name TEXT NOT NULL,
            cover_url TEXT,
            store_url TEXT,
            added_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)

    # Check if cover_url and store_url exist, and add them if missing
    try:
        cursor.execute("ALTER TABLE wishlist ADD COLUMN cover_url TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE wishlist ADD COLUMN store_url TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()

init_db()

# Utility functions
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def register_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    hashed_password = hash_password(password)
    try:
        cursor.execute("""
            INSERT INTO users (username, password) VALUES (?,?)
        """, (username, hashed_password))
        conn.commit()
        st.success("Registration successful! You can now log in.")
    except sqlite3.IntegrityError:
        st.error("Username already exists.")
    finally:
        conn.close()

def login_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    hashed_password = hash_password(password)
    cursor.execute("""
        SELECT user_id FROM users WHERE username = ? AND password = ?
    """, (username, hashed_password))
    user = cursor.fetchone()
    conn.close()
    return user

def logout_user():
    if "user_id" in st.session_state:
        del st.session_state["user_id"]
        st.success("Logged out successfully.")

def extract_user_id(steam_url):
    try:
        parsed_url = urlparse(steam_url)
        path_segments = parsed_url.path.strip('/').split('/')
        if len(path_segments) > 1 and path_segments[0] in ['profiles', 'id']:
            return path_segments[-1]  # This will return the vanity URL part
        else:
            st.error("Invalid Steam URL format. Please use a valid profile or ID URL.")
            return None
    except Exception as e:
        st.error(f"Error parsing URL: {e}")
        return None

def resolve_vanity_url(vanity_url):
    url = f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/?key={STEAM_API_KEY}&vanityurl={vanity_url}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        if data.get("response", {}).get("success") == 1:
            return data["response"].get("steamid")
        else:
            st.error("Could not resolve vanity URL. Ensure the vanity URL is correct.")
    else:
        st.error(f"Failed to resolve vanity URL. Steam API returned: {response.status_code}")
    return None

def fetch_game_news(app_id, steam_api_key):
    """Fetch recent news for a game by its Steam App ID."""
    url = f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    params = {
        "appid": app_id,
        "count": 3,       # Number of news articles to fetch
        "maxlength": 300, # Max length of news content
        "format": "json"
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        news_items = data.get("appnews", {}).get("newsitems", [])
        return news_items if news_items else None
    else:
        return None

# Function to display news or a fallback message
def display_game_news(app_id, steam_api_key):
    """Display news or a fallback message for the specified game."""
    news = fetch_game_news(app_id, steam_api_key)
    if news:
        print(f"Recent News for Game (App ID: {app_id}):")
        for article in news:
            print(f"- {article['title']}: {article['contents'][:100]}...")
            print(f"  Read more: {article['url']}\n")
    else:
        print(f"No recent news or patches available for this game (App ID: {app_id}).")

def fetch_owned_games(steamid):
    url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": steamid,
        "include_appinfo": True,
        "include_played_free_games": True
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        games = response.json().get("response", {}).get("games", [])
        if not games:
            st.error("No games found. Ensure the Steam64 ID is correct and your games are Public.")
        return games
    else:
        st.error(f"Failed to fetch games. Steam API returned: {response.status_code} - {response.text}")
        return []

def add_games_to_db(games, user_id, steam_user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    for game in games:
        # Check if the game already exists
        cursor.execute("""
            SELECT playtime FROM games WHERE steam_game_id = ? AND user_id = ? AND steam_user_id = ?
        """, (game["appid"], user_id, steam_user_id))
        existing_game = cursor.fetchone()

        if existing_game:
            # Update playtime if it has increased
            if game["playtime_forever"] > existing_game[0]:
                cursor.execute("""
                    UPDATE games
                    SET playtime = ?, added_on = CURRENT_TIMESTAMP
                    WHERE steam_game_id = ? AND user_id = ? AND steam_user_id = ?
                """, (game["playtime_forever"], game["appid"], user_id, steam_user_id))
        else:
            # Insert new game
            genres, cover_url, store_url, description, name = fetch_game_details(game["appid"], game["name"])
            cursor.execute("""
                INSERT INTO games (steam_game_id, game_name, playtime, genres, cover_url, store_url, added_on, user_id, steam_user_id)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
            """, (game["appid"], name, game["playtime_forever"], genres, cover_url, store_url, user_id, steam_user_id))
    conn.commit()
    conn.close()

def save_review_to_db(game_id, game_name, review_text, rating):
    """Save a user's review for a searched game to the database."""
    user_id = st.session_state.get("user_id")  # Ensure the user is logged in
    if not user_id:
        st.error("You must be logged in to save a review.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # Check if the game exists in the database
        cursor.execute("""
            SELECT id FROM games WHERE steam_game_id = ? AND user_id = ?
        """, (game_id, user_id))
        existing_game = cursor.fetchone()

        if not existing_game:
            # Add the game to the database
            cursor.execute("""
                INSERT INTO games (steam_game_id, game_name, user_id, added_on)
                VALUES (?, ?, ?, ?)
            """, (game_id, game_name, user_id, datetime.now()))
            game_db_id = cursor.lastrowid
        else:
            game_db_id = existing_game[0]

        # Add the review
        cursor.execute("""
            INSERT INTO reviews (user_id, game_id, review_text, rating, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, game_db_id, review_text, rating, datetime.now()))
        conn.commit()
    except Exception as e:
        st.error(f"Error saving review: {e}")
    finally:
        conn.close()

def has_existing_review(user_id, game_id):
    """Check if a user has already reviewed a specific game."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT review_id FROM reviews r
            JOIN games g ON r.game_id = g.id
            WHERE r.user_id = ? AND g.steam_game_id = ?
        """, (user_id, game_id))
        existing_review = cursor.fetchone()
        return bool(existing_review)
    except Exception as e:
        st.error(f"Error checking existing review: {e}")
        return False
    finally:
        conn.close()

def search_game_by_name_steam(name):
    """Search for a game by name using Steam Store search."""
    search_url = f"https://store.steampowered.com/search/?term={name.replace(' ', '+')}"
    response = requests.get(search_url)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for game in soup.find_all("a", class_="search_result_row"):
            appid = game.get("data-ds-appid")
            if appid:
                name = game.find("span", class_="title").text
                image = game.find("img").get("src", "")
                results.append({"appid": appid, "name": name, "image": image})
        if not results:
            st.error("No results found on Steam Store.")
        return results
    else:
        st.error("Failed to search for games. Steam Store returned an error.")
        st.write(f"Error Details: {response.status_code} - {response.text}")  # Debug log
        return []

# Full updated search_and_display_games function
def search_and_display_games():
    """Search for games by name and display details along with recent news."""
    st.header("Search for Steam Games")

    # Ensure session state variables exist
    if "search_results" not in st.session_state:
        st.session_state["search_results"] = None
    if "last_search" not in st.session_state:
        st.session_state["last_search"] = ""

    # Input for game name
    game_name = st.text_input("Enter the Game Name:", placeholder="e.g., Dota 2")

    # Search button logic
    if st.button("Search by Name"):
        if game_name.strip():  # Ensure the input is not empty or just whitespace
            st.session_state["last_search"] = game_name
            search_results = search_game_by_name_steam(game_name)
            if search_results:
                st.session_state["search_results"] = search_results
            else:
                st.session_state["search_results"] = None
                st.error("No results found for the entered name.")
        else:
            st.error("Please enter a valid game name.")

    # Display search results
    if st.session_state["search_results"]:
        st.subheader(f"Search Results for: {st.session_state['last_search']}")
        
        for game in st.session_state["search_results"]:
            # Fetch additional details for the game
            genres, cover_url, store_url, description, name = fetch_game_details(game["appid"], game["name"])
            
            col1, col2 = st.columns([1, 2])
            
            with col1:
                st.image(cover_url, width=150)
            
            with col2:
                st.write(f"**Name:** {name}")
                st.write(f"**Genres:** {genres}")
                st.write(f"**Description:** {description}")
                st.write(f"[View on Steam]({store_url})")

                # Wishlist button logic
                user_id = st.session_state.get("user_id")
                if user_id:
                    if not is_game_in_wishlist(user_id, game["appid"]):
                        if st.button(f"Add to Wishlist: {name}", key=f"wishlist_{game['appid']}"):
                            add_to_wishlist(user_id, game["appid"], name, cover_url, store_url)
                    else:
                        st.info(f"{name} is already in your wishlist!")
                else:
                    st.error("Please log in to save games to your wishlist.")

            # Review Section
            if user_id and has_existing_review(user_id, game["appid"]):
                st.info(f"You have already reviewed {name}. You can edit your review from the 'Your Reviews' page.")
            else:
                with st.expander(f"Review {name}"):
                    review = st.text_area(f"Review for {name}", key=f"review_{game['appid']}")
                    rating = st.slider(f"Rate {name}", 1, 5, key=f"rating_{game['appid']}")

                    if st.button(f"Submit Review for {name}", key=f"submit_review_{game['appid']}"):
                        if user_id:
                            add_or_update_review(user_id, game["appid"], name, review, rating)
                            st.success(f"Your review for {name} has been saved!")
                        else:
                            st.error("Please log in to submit a review.")

            # News Section
            with st.expander(f"Recent News for {name}"):
                news = fetch_game_news(game["appid"], STEAM_API_KEY)
                if news:
                    for article in news:
                        st.markdown(f"- **[{article['title']}]({article['url']})**")
                else:
                    st.write("No recent news or patches available for this game.")
            
            st.divider()
    else:
        if st.session_state["last_search"]:
            st.error("No results found for your search. Try another game name.")

# Add this function to check if a game is already in wishlist
def is_game_in_wishlist(user_id, steam_game_id):
    """Check if a game is already in the user's wishlist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id FROM wishlist 
            WHERE user_id = ? AND steam_game_id = ?
        """, (user_id, steam_game_id))
        result = cursor.fetchone()
        return bool(result)
    except Exception as e:
        st.error(f"Error checking wishlist: {e}")
        return False
    finally:
        conn.close()

def add_or_update_review(user_id, game_id, game_name, review_text, rating):
    """Add or update a review for a game in the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # Check if the game exists in the database
        cursor.execute("""
            SELECT id FROM games WHERE steam_game_id = ? AND user_id = ?
        """, (game_id, user_id))
        game_entry = cursor.fetchone()

        if not game_entry:
            # Insert the game if it doesn't exist, ensuring game_name is saved
            cursor.execute("""
                INSERT INTO games (steam_game_id, game_name, user_id, added_on)
                VALUES (?, ?, ?, ?)
            """, (game_id, game_name, user_id, datetime.now()))
            game_entry_id = cursor.lastrowid
        else:
            game_entry_id = game_entry[0]

        # Check if a review already exists
        cursor.execute("""
            SELECT review_id FROM reviews WHERE user_id = ? AND game_id = ?
        """, (user_id, game_entry_id))
        existing_review = cursor.fetchone()

        if existing_review:
            # Update the existing review
            cursor.execute("""
                UPDATE reviews
                SET review_text = ?, rating = ?, created_at = CURRENT_TIMESTAMP
                WHERE review_id = ?
            """, (review_text, rating, existing_review[0]))
        else:
            # Add a new review
            cursor.execute("""
                INSERT INTO reviews (user_id, game_id, review_text, rating, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, game_entry_id, review_text, rating, datetime.now()))
        conn.commit()
        st.success(f"Review for '{game_name}' has been added/updated successfully!")
    except Exception as e:
        st.error(f"Error saving review: {e}")
    finally:
        conn.close()

def fetch_game_details(appid, game_name):
    name = game_name
    genres = "Unknown"
    cover_url = "https://via.placeholder.com/150"
    store_url = f"https://store.steampowered.com/app/{appid}"
    description = "No description available."

    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    response = requests.get(url)
    if response.status_code == 200:
        game_data = response.json().get(str(appid), {}).get("data", {})
        if game_data:
            name = game_data.get("name", game_name)
            genres = ", ".join([genre['description'] for genre in game_data.get("genres", [])])
            cover_url = game_data.get("header_image", cover_url)
            description = game_data.get("short_description", description)

    return genres, cover_url, store_url, description, name

def get_games_from_db(user_id, steam_user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("""
        SELECT * FROM games WHERE user_id = ? AND steam_user_id = ?
    """, (user_id, steam_user_id))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_user_reviews(user_id):
    """Retrieve all reviews submitted by the logged-in user."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT r.review_id, g.game_name, r.review_text, r.rating, r.created_at
            FROM reviews r
            JOIN games g ON r.game_id = g.id
            WHERE r.user_id = ?
        """, (user_id,))
        reviews = cursor.fetchall()
        return reviews
    except Exception as e:
        st.error(f"Error fetching reviews: {e}")
        return []
    finally:
        conn.close()

def update_review(review_id, new_text, new_rating):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE reviews
            SET review_text = ?, rating = ?, created_at = CURRENT_TIMESTAMP
            WHERE review_id = ?
        """, (new_text, new_rating, review_id))
        conn.commit()
        st.success("Review updated successfully!")
    except Exception as e:
        st.error(f"Error updating review: {e}")
    finally:
        conn.close()

def delete_review(review_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            DELETE FROM reviews WHERE review_id = ?
        """, (review_id,))
        conn.commit()
        st.success("Review deleted successfully!")
    except Exception as e:
        st.error(f"Error deleting review: {e}")
    finally:
        conn.close()

def handle_steam_url_input():
    steam_url = st.text_input("Enter your Steam Profile URL:", placeholder="https://steamcommunity.com/profiles/76561198882302331")
    if st.button("Fetch My Steam Games"):
        if steam_url:
            user_id_or_vanity = extract_user_id(steam_url)
            if user_id_or_vanity:
                if user_id_or_vanity.isdigit():
                    return user_id_or_vanity
                else:
                    resolved_id = resolve_vanity_url(user_id_or_vanity)
                    if resolved_id:
                        return resolved_id
                    else:
                        st.error("Could not resolve vanity URL to a Steam64 ID. Please ensure the URL is correct.")
                        return None
            else:
                st.error("Invalid Steam URL format. Please try again.")
                return None
        else:
            st.error("Please enter a valid Steam Profile URL.")
            return None

def get_username(user_id):
    """Fetch username based on user_id."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        st.error(f"Error fetching username: {e}")
        return None
    finally:
        conn.close()

def get_user_reviews_for_ai(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT g.game_name, r.review_text, r.rating 
            FROM reviews r
            JOIN games g ON r.game_id = g.id
            WHERE r.user_id = ?
        """, (user_id,))
        reviews = cursor.fetchall()
        
        # Debugging: Check fetched reviews
        st.write("Fetched Reviews:", reviews)
        
        return reviews
    except Exception as e:
        st.error(f"Error fetching reviews: {e}")
        return []
    finally:
        conn.close()



def add_to_wishlist(user_id, steam_game_id, game_name, cover_url, store_url):
    """Add a game to the user's wishlist if it is not already present."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # Check if the game is already in the wishlist
        cursor.execute("""
            SELECT id FROM wishlist WHERE user_id = ? AND steam_game_id = ?
        """, (user_id, steam_game_id))
        existing_entry = cursor.fetchone()

        if existing_entry:
            st.warning(f"'{game_name}' is already in your wishlist!")
        else:
            # Add the game to the wishlist
            cursor.execute("""
                INSERT INTO wishlist (user_id, steam_game_id, game_name, cover_url, store_url, added_on)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, steam_game_id, game_name, cover_url, store_url))
            conn.commit()
            st.success(f"'{game_name}' has been added to your wishlist!")
    except Exception as e:
        st.error(f"Error adding game to wishlist: {e}")
    finally:
        conn.close()

def remove_from_wishlist(user_id, steam_game_id):
    """Remove a game from the user's wishlist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            DELETE FROM wishlist WHERE user_id = ? AND steam_game_id = ?
        """, (user_id, steam_game_id))
        conn.commit()
        st.success("Game removed from your wishlist.")
    except Exception as e:
        st.error(f"Error removing game from wishlist: {e}")
    finally:
        conn.close()

def fetch_wishlist(user_id):
    """Fetch all games in the user's wishlist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT steam_game_id, game_name, cover_url, store_url, added_on
            FROM wishlist WHERE user_id = ?
        """, (user_id,))
        return cursor.fetchall()
    except Exception as e:
        st.error(f"Error fetching wishlist: {e}")
        return []
    finally:
        conn.close()

# Generate recommendations using Google Gemini
def generate_recommendations(user_id, limit=10):
    reviews = get_user_reviews_for_ai(user_id)
    
    # Check if reviews are retrieved
    if not reviews:
        return [{"name": "No Reviews Found", "description": "Please submit reviews for better recommendations."}]

    # Prepare the input prompt for the AI
    review_text = "\n".join([f"{game}: {review} (Rating: {rating}/5)" for game, review, rating in reviews])
    prompt = f"""
    Based on these game reviews, recommend {limit} Steam games (not other games like minecraft. Game has to be on steam) the user should try. DO NOT SAY GAMES THE USER ALREADY HAS IN THEIR REVIEWS SAY OTHER GAMES
    Provide each game's name, a short description, its genre, and a brief premise:
    
    {review_text}
    """
    
    try:
        # Generate recommendations
        response = model.generate_content(prompt)
        
        # Debugging: Display the response
        st.write("Response from Gemini:", response.text)
        
        # Parse the response
        recommendations = []
        for line in response.text.split("\n"):
            if line.strip():
                parts = line.split(" - ")
                if len(parts) >= 3:
                    name, description, genre_premise = parts[0], parts[1], " - ".join(parts[2:])
                    recommendations.append({
                        "name": name.strip(),
                        "description": description.strip(),
                        "genres": genre_premise.strip()
                    })
        return recommendations[:limit]
    except Exception as e:
        st.error(f"Error generating recommendations: {e}")
        return [{"name": "Error", "description": "Could not fetch recommendations due to an error."}]

# Streamlit UI
st.set_page_config(page_title="Steam Recommendations", layout="wide")
st.sidebar.title("Navigation")
page = st.sidebar.radio("Select a page:", ["Register", "Login", "Add Steam Account", "Your Games", "Recommendations", "Your Reviews","Search Games", "My Wishlist", "Logout"])

if page == "Register":
    st.header("Create an Account")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Register"):
        if username and password:
            register_user(username, password)
        else:
            st.error("Please fill in all fields.")

elif page == "Login":
    st.header("Log In")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Log In"):
        user = login_user(username, password)
        if user:
            st.session_state.user_id = user[0]
            st.success("Logged in successfully!")
        else:
            st.error("Invalid username or password.")

elif page == "Logout":
    logout_user()

else:
    if "user_id" not in st.session_state:
        st.warning("Please log in to access this page.")
        st.stop()

    user_id = st.session_state.user_id

    if page == "Add Steam Account":
        st.header("Add Your Steam Account")
        steam_user_id = handle_steam_url_input()
        if steam_user_id:
            games = fetch_owned_games(steam_user_id)
            if games:
                add_games_to_db(games, st.session_state.user_id, steam_user_id)
                st.success(f"Fetched {len(games)} games from your Steam library!")
            else:
                st.warning("No games found. Please check your Steam Profile URL or ensure your games are set to Public.")
    
    elif page == "My Wishlist":
        st.header("Your Wishlist")
    
        # Fetch the wishlist for the logged-in user
        wishlist = fetch_wishlist(user_id)
    
        if wishlist:
            for steam_game_id, game_name, cover_url, store_url, added_on in wishlist:
                # Display game image
                st.image(cover_url, width=150)
            
                # Display game name as a hyperlink to the Steam store
                st.write(f"**Name:** [{game_name}]({store_url})")
            
                # Show the timestamp when the game was added
                st.write(f"**Added on:** {added_on}")
            
                # Option to remove the game from the wishlist
                if st.button(f"Remove from Wishlist: {game_name}", key=f"remove_{steam_game_id}"):
                    remove_from_wishlist(user_id, steam_game_id)
        else:
            # If the wishlist is empty
            st.write("Your wishlist is empty.")

    # "Your Games" Section with Steam Account Labeling Feature
    elif page == "Your Games":
        st.header("Your Steam Games")

        # Fetch Steam accounts linked to the user
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT steam_user_id, 
                            (SELECT label FROM accounts WHERE steam_user_id = g.steam_user_id AND label IS NOT NULL LIMIT 1) AS label
            FROM games g WHERE user_id = ?
        """, (user_id,))
        steam_accounts = cursor.fetchall()
        conn.close()

        if not steam_accounts:
            st.write("No Steam accounts linked. Please add your Steam account first.")
        else:
            # Display Steam accounts in a dropdown with an option to label accounts
            options = [f"{account[1] or 'No Label'} ({account[0]})" for account in steam_accounts]
            selected_account = st.selectbox("Select Steam Account:", options)

            if selected_account and "None" not in selected_account:
                steam_user_id = steam_accounts[options.index(selected_account)][0]

                # Labeling Feature
                st.subheader("Label Your Steam Account")
                existing_label = steam_accounts[options.index(selected_account)][1] or ""
                new_label = st.text_input("Set a label for this account:", value=existing_label)

                if st.button("Save Label"):
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    try:
                        cursor.execute("""
                            INSERT INTO accounts (steam_user_id, label) 
                            VALUES (?, ?) 
                            ON CONFLICT(steam_user_id) DO UPDATE SET label = ?
                        """, (steam_user_id, new_label, new_label))
                        conn.commit()
                        st.success("Label saved successfully!")
                    except Exception as e:
                        st.error(f"Error saving label: {e}")
                    finally:
                        conn.close()

                # Enable Refresh Library button only if a valid account is selected
                if st.button("Refresh Library"):
                    games = fetch_owned_games(steam_user_id)
                    if games:
                        add_games_to_db(games, user_id, steam_user_id)
                        st.success(f"Library refreshed! Updated playtime and added any new games.")
                    else:
                        st.warning("No games found or unable to fetch from Steam.")

                # Fetch and display games from the database
                games = get_games_from_db(user_id, steam_user_id)
                if games:
                    sort_by = st.selectbox("Sort by:", ["Playtime", "Name"])
                    filter_genre = st.text_input("Filter by genre:")
                    games = sorted(games, key=lambda x: x[3], reverse=True) if sort_by == "Playtime" else sorted(games, key=lambda x: x[2])

                    for game in games:
                        if filter_genre.lower() in game[4].lower():
                            col1, col2 = st.columns([1, 2])

                            with col1:
                                st.image(game[5], width=150)

                            with col2:
                                st.write(f"**[{game[2]}]({game[6]})**")
                                st.write(f"**Playtime:** {game[3]} minutes")
                                st.write(f"**Genres:** {game[4]}")

                                # Add Wishlist button if it's not the user's own game
                                if steam_user_id != st.session_state.get("steam_id"):
                                    if not is_game_in_wishlist(user_id, game[1]):  # game[1] is steam_game_id
                                        if st.button(f"Add to Wishlist: {game[2]}", key=f"wishlist_{game[1]}"):
                                            add_to_wishlist(user_id, game[1], game[2], game[5], game[6])
                                    else:
                                        st.info(f"{game[2]} is already in your wishlist!")

                            # Review Section
                            if has_existing_review(user_id, game[1]):
                                st.info(f"You have already reviewed {game[2]}. You can edit your review from the 'Your Reviews' page.")
                            else:
                                with st.expander(f"Review {game[2]}"):
                                    review = st.text_area(f"Leave a review for {game[2]}", key=f"review_{game[0]}")
                                    rating = st.slider(f"Rate {game[2]}", 1, 5, key=f"rating_{game[0]}")
                                    if st.button(f"Submit Review for {game[2]}", key=f"submit_{game[0]}"):
                                        add_or_update_review(user_id, game[1], game[2], review, rating)
                                        st.success("Review submitted successfully.")

                            st.divider()
                else:
                    st.write("You don't own any games on this account.")
            else:
                st.warning("Please select a valid Steam account to view the library.")

    elif page == "Your Reviews":
        st.header("Your Reviews And Notes")
        user_id = st.session_state.get("user_id")
        if not user_id:
            st.warning("You must be logged in to view your reviews.")
        else:
            reviews = get_user_reviews(user_id)
            if reviews:
                for review_id, game_name, review_text, rating, created_at in reviews:
                    st.subheader(f"{game_name}")
                    st.write(f"**Rating:** {rating}/5")
                    st.write(f"**Review:** {review_text}")
                    st.write(f"**Date:** {created_at}")

                    # Option to edit the review
                    new_review_text = st.text_area(f"Edit Review for {game_name}", value=review_text, key=f"edit_text_{review_id}")
                    new_rating = st.slider(f"Edit Rating for {game_name}", 1, 5, value=rating, key=f"edit_rating_{review_id}")
                    if st.button(f"Save Changes to Review for {game_name}", key=f"edit_button_{review_id}"):
                        update_review(review_id, new_review_text, new_rating)
                        st.success(f"Review for {game_name} updated!")

                    # Option to delete the review
                    if st.button(f"Delete Review for {game_name}", key=f"delete_button_{review_id}"):
                        delete_review(review_id)
            else:
                st.write("You have not submitted any reviews yet.")

        # Streamlit Recommendations Tab
    elif page == "Recommendations":
        st.header("Game Recommendations")
    
        # Fetch and display username
        username = get_username(user_id)
        if username:
            st.write(f"Welcome, **{username}**! Here are your personalized game recommendations.")
        else:
            st.write("Welcome! Here are your personalized game recommendations.")

        # Generate recommendations if not already in session state
        if "rec_data" not in st.session_state or not st.session_state.rec_data:
            st.session_state.rec_data = generate_recommendations(user_id, limit=10)

        # Display recommendations
        if st.session_state.rec_data:
            for rec in st.session_state.rec_data:
                st.write(f"**Name:** {rec['name']}")
                st.write(f"**Description:** {rec['description']}")
                st.write(f"**Genres and Premise:** {rec.get('genres', 'No genres available')}")
                st.write("---")
        else:
            st.write("No more recommendations available at this time.")
        
    elif page == "Search Games":
        search_and_display_games()