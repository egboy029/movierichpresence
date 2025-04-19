import os
import json
import time
import re
import sys
import logging
import warnings
import asyncio
import atexit
from typing import Dict, Optional, Union
from dotenv import load_dotenv
import requests
from pypresence import Presence
import psutil
import win32gui
import win32process
import ctypes

# Suppress specific warnings
warnings.filterwarnings("ignore", message="There is no current event loop", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="Task was destroyed but it is pending!", category=RuntimeWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("discord_presence.log")
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISNEY_CLIENT_ID = os.getenv("DISNEY_CLIENT_ID") or DISCORD_CLIENT_ID
NETFLIX_CLIENT_ID = os.getenv("NETFLIX_CLIENT_ID") or DISCORD_CLIENT_ID
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_API_BASE = 'https://api.themoviedb.org/3'
UPDATE_INTERVAL = 15  # seconds

# Constants
NETFLIX_APP_NAME = "Netflix"
DISNEY_APP_NAME = "Disney+"
NETFLIX_DOMAINS = ["netflix.com", "www.netflix.com"]
DISNEY_DOMAINS = ["disneyplus.com", "www.disneyplus.com"]
NETFLIX_PROCESS_NAMES = ["Netflix.exe", "WWAHost.exe"]  # WWAHost.exe is for UWP version
DISNEY_PROCESS_NAMES = ["Disney+.exe", "WWAHost.exe"]   # WWAHost.exe is for UWP version

# Browser detection
SUPPORTED_BROWSERS = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave"
}

# Track current media state
current_media = None
start_timestamp = None

def get_active_window_title() -> Optional[str]:
    """Get the title of the currently active window."""
    return win32gui.GetWindowText(win32gui.GetForegroundWindow())

def get_process_name_by_hwnd(hwnd: int) -> Optional[str]:
    """Get process name from window handle."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)
        return process.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None

def enum_windows_callback(hwnd: int, windows: list) -> bool:
    """Callback for EnumWindows to get all window titles."""
    # Include all windows, not just visible ones
    window_title = win32gui.GetWindowText(hwnd)
    if window_title:
        process_name = get_process_name_by_hwnd(hwnd)
        windows.append((hwnd, window_title, process_name))
    return True

def get_all_windows() -> list:
    """Get all visible windows with their titles and process names."""
    windows = []
    # Capture all windows, not just visible ones
    win32gui.EnumWindows(lambda hwnd, windows: enum_windows_callback(hwnd, windows), windows)
    return windows

def clean_title_for_logging(title: str) -> str:
    """Clean title string for logging to avoid Unicode encoding errors."""
    if not title:
        return ""
    try:
        # Remove zero-width spaces and other problematic characters
        cleaned = title.replace('\u200b', '').replace('\u200e', '')
        # Remove other invisible/control characters
        cleaned = ''.join(char for char in cleaned if char.isprintable() or char.isspace())
        # Replace any other non-ASCII characters that might cause issues
        return cleaned.encode('ascii', 'replace').decode('ascii')
    except Exception as e:
        # Super-safe fallback
        try:
            # Try to remove all non-ASCII characters completely
            return ''.join(char for char in title if ord(char) < 128)
        except:
            # Ultimate fallback
            return "Title with encoding issues"

def clean_title(title: str, service: str) -> str:
    """Clean title by removing redundant service information."""
    if not title:
        return ""
        
    # Remove service name from title
    if service == "Disney+":
        patterns = [
            " | Disney+",
            " - Disney+",
            " – Disney+",
            "Disney+ - ",
            " Disney+"
        ]
        for pattern in patterns:
            title = title.replace(pattern, "")
            
    elif service == "Netflix":
        patterns = [
            " - Netflix",
            " | Netflix",
            "Netflix - ",
            " Netflix"
        ]
        for pattern in patterns:
            title = title.replace(pattern, "")
    
    # Remove browser window information from the title
    title = re.sub(r'\s+and \d+ more pages.*', '', title)
    title = re.sub(r'\s+- Personal.*', '', title)
    title = re.sub(r'\s+- Microsoft.*Edge', '', title)
    title = re.sub(r'\s+- Google Chrome', '', title)
    title = re.sub(r'\s+- Brave', '', title)
    title = re.sub(r'\s+- Firefox', '', title)
    
    # Remove "Watch " prefix often seen in Disney+ titles
    if title.startswith("Watch "):
        title = title[6:]

    # Remove the word "on" if it appears alone - important for fixing "on" text issue
    title = re.sub(r'\s+on\s*$', '', title)  # "on" at the end
    title = re.sub(r'^\s*on\s+', '', title)  # "on" at the start
    title = re.sub(r'\s+on\s+', ' ', title)  # "on" in the middle
        
    # Clean up any leftover separators
    title = title.strip(" |:-–—")
        
    return title.strip()

def parse_netflix_title(title: str) -> Optional[Dict[str, Union[str, int]]]:
    """Parse Netflix window title to extract media information."""
    # Remove "Netflix - " prefix if present
    if title.startswith("Netflix - "):
        title = title[10:]
    
    # Common pattern for shows: "Show Name: S1:E1 Episode Title"
    show_pattern = r"(.*?):\s+S(\d+):E(\d+)(?:\s+(.+))?"
    show_match = re.match(show_pattern, title)
    
    if show_match:
        return {
            "title": show_match.group(1).strip(),
            "type": "show",
            "season": int(show_match.group(2)),
            "episode": int(show_match.group(3)),
            "episodeTitle": show_match.group(4).strip() if show_match.group(4) else None
        }
    
    # If not a show pattern, assume it's a movie
    return {
        "title": title.strip(),
        "type": "movie"
    }

def parse_disney_title(title: str) -> Optional[Dict[str, Union[str, int]]]:
    """Parse Disney+ window title to extract media information."""
    # Clean the title first - remove Disney+ text
    clean_title = title
    
    # Remove "Disney+ - " prefix if present
    if title.startswith("Disney+ - "):
        clean_title = title[10:]
    
    # Remove other Disney+ formats
    for pattern in [" | Disney+", " - Disney+", " – Disney+", "Disney+"]:
        if pattern in clean_title:
            clean_title = clean_title.replace(pattern, "")
    
    clean_title = clean_title.strip()
    
    # Common pattern for shows: "Show Name - S01E01 - Episode Title"
    show_pattern = r"(.*?)\s+-\s+S(\d+)E(\d+)(?:\s+-\s+(.+))?"
    show_match = re.match(show_pattern, clean_title)
    
    if show_match:
        # Extract and clean the episode title
        episode_title = None
        if show_match.group(4):
            episode_title = show_match.group(4).strip()
            # Remove redundant Disney+ in episode title too
            for pattern in [" | Disney+", " - Disney+", " – Disney+", "Disney+"]:
                if pattern in episode_title:
                    episode_title = episode_title.replace(pattern, "")
            episode_title = episode_title.strip()
            
        return {
            "title": show_match.group(1).strip(),
            "type": "show",
            "season": int(show_match.group(2)),
            "episode": int(show_match.group(3)),
            "episodeTitle": episode_title
        }
    
    # If not a show pattern, assume it's a movie
    return {
        "title": clean_title.strip(),
        "type": "movie"
    }

def check_browser_tabs() -> Optional[Dict[str, Union[str, bool, int]]]:
    """Check browser tabs for Netflix or Disney+ content."""
    disney_content = None
    netflix_content = None
    
    # Track all potential streaming windows we find
    streaming_windows = []
    
    # Try to find running browsers
    for browser_process, browser_name in SUPPORTED_BROWSERS.items():
        if any(proc.name().lower() == browser_process.lower() for proc in psutil.process_iter()):
            try:
                # Get all windows for this browser
                windows = get_all_windows()
                
                # First pass: Check all browser windows for Disney+ and Netflix content
                for window_hwnd, title, process_name in windows:
                    if not process_name or not title:
                        continue
                    
                    # Only look at windows for this browser
                    if process_name.lower() != browser_process.lower():
                        continue
                        
                    # Clean title for logging to avoid Unicode errors
                    clean_log_title = clean_title_for_logging(title)    
                    logger.debug(f"Browser window title: '{clean_log_title}'")
                    
                    # STRICT FILTERING: Filter out readme.md and other documentation files
                    if any(pattern in title.lower() for pattern in [
                        "readme", ".md", "documentation", "github", ".txt", 
                        "license", "coding", "programming", "developer"
                    ]):
                        logger.info(f"Skipping documentation file: {clean_log_title}")
                        continue
                    
                    # Check if it's Netflix
                    if " - Netflix" in title or "Netflix" in title:
                        # Additional screening - make sure it's not just a README or documentation
                        if "readme" in title.lower() or ".md" in title.lower():
                            logger.info(f"Skipping false Netflix detection in documentation: {clean_log_title}")
                            continue
                            
                        clean_title = clean_title(title, "Netflix")
                        logger.info(f"Detected Netflix in browser: '{clean_title_for_logging(clean_title)}'")
                        
                        # Try to detect if it's a show with episode info
                        show_match = re.search(r'(.*?):\s+S(\d+):E(\d+)(?:\s+(.+))?', clean_title)
                        if show_match:
                            netflix_content = {
                                "isWatching": True,
                                "service": "Netflix",
                                "title": show_match.group(1).strip(),
                                "type": "show",
                                "season": int(show_match.group(2)),
                                "episode": int(show_match.group(3)),
                                "episodeTitle": show_match.group(4).strip() if show_match.group(4) else None,
                                "window_hwnd": window_hwnd,
                                "is_visible": win32gui.IsWindowVisible(window_hwnd),
                                "is_running": True
                            }
                        else:
                            netflix_content = {
                                "isWatching": True,
                                "service": "Netflix",
                                "title": clean_title,
                                "type": "movie",
                                "window_hwnd": window_hwnd,
                                "is_visible": win32gui.IsWindowVisible(window_hwnd),
                                "is_running": True
                            }
                        streaming_windows.append(netflix_content)
                    
                    # Check for Disney+ in multiple ways
                    is_disney = False
                    for pattern in ["Disney+", "disneyplus"]:
                        if pattern.lower() in title.lower():
                            is_disney = True
                            break
                    
                    if is_disney:
                        # Additional screening - make sure it's not just a README or documentation
                        if "readme" in title.lower() or ".md" in title.lower():
                            logger.info(f"Skipping false Disney+ detection in documentation: {clean_log_title}")
                            continue
                            
                        clean_title = clean_title(title, "Disney+")
                        logger.info(f"Detected Disney+ in browser: '{clean_title_for_logging(clean_title)}'")
                        
                        # Try to detect if it's a show with episode info
                        show_match = re.search(r'(.*?)\s+-\s+S(\d+)E(\d+)(?:\s+-\s+(.+))?', clean_title)
                        if show_match:
                            disney_content = {
                                "isWatching": True,
                                "service": "Disney+",
                                "title": show_match.group(1).strip(),
                                "type": "show",
                                "season": int(show_match.group(2)),
                                "episode": int(show_match.group(3)),
                                "episodeTitle": show_match.group(4).strip() if show_match.group(4) else None,
                                "window_hwnd": window_hwnd,
                                "is_visible": win32gui.IsWindowVisible(window_hwnd),
                                "is_running": True
                            }
                        else:
                            disney_content = {
                                "isWatching": True,
                                "service": "Disney+",
                                "title": clean_title,
                                "type": "movie",
                                "window_hwnd": window_hwnd,
                                "is_visible": win32gui.IsWindowVisible(window_hwnd),
                                "is_running": True
                            }
                        streaming_windows.append(disney_content)
                    
                # Additional check for URLs in browser windows
                for window_hwnd, title, process_name in windows:
                    if not process_name or not title:
                        continue
                        
                    if process_name.lower() == browser_process.lower():
                        # Look for URLs in title
                        if "disneyplus.com" in title.lower() or "disney+" in title.lower():
                            clean_title = clean_title(title, "Disney+")
                            if not clean_title or clean_title.lower() in ["disney+ | disney+", "disney+"]:
                                clean_title = "Disney+ Content"
                                
                            logger.info(f"Detected Disney+ by URL: '{clean_title_for_logging(clean_title)}'")
                            
                            streaming_windows.append({
                                "isWatching": True,
                                "service": "Disney+",
                                "title": clean_title,
                                "type": "unknown",
                                "window_hwnd": window_hwnd,
                                "is_visible": win32gui.IsWindowVisible(window_hwnd),
                                "is_running": True
                            })
                            
                        elif "netflix.com" in title.lower():
                            clean_title = clean_title(title, "Netflix")
                            if not clean_title or clean_title.lower() in ["netflix", "home - netflix"]:
                                clean_title = "Netflix Content"
                                
                            logger.info(f"Detected Netflix by URL: '{clean_title_for_logging(clean_title)}'")
                            
                            streaming_windows.append({
                                "isWatching": True,
                                "service": "Netflix",
                                "title": clean_title,
                                "type": "unknown",
                                "window_hwnd": window_hwnd,
                                "is_visible": win32gui.IsWindowVisible(window_hwnd),
                                "is_running": True
                            })
                
            except Exception as e:
                logger.error(f"Error checking browser tabs: {e}")
                continue
    
    # No streaming content found
    if not streaming_windows:
        return None
    
    # IMPORTANT: Modified prioritization logic:
    # Always use any detected streaming window, regardless of visibility
    # - This ensures the presence stays active even if the window is minimized
    
    # First, still prefer active window if available
    active_window_hwnd = win32gui.GetForegroundWindow()
    for window in streaming_windows:
        if window["window_hwnd"] == active_window_hwnd:
            window_copy = window.copy()
            del window_copy["window_hwnd"]
            del window_copy["is_visible"]
            del window_copy["is_running"]
            logger.info(f"Selected active streaming window: {window_copy['service']} - {window_copy['title']}")
            return window_copy
    
    # Next, if any window is found (even if minimized/background), use it 
    if streaming_windows:
        window = streaming_windows[0]
        window_copy = window.copy()
        del window_copy["window_hwnd"]
        del window_copy["is_visible"]
        del window_copy["is_running"]
        if window["is_visible"]:
            logger.info(f"Selected visible streaming window: {window_copy['service']} - {window_copy['title']}")
        else:
            logger.info(f"Selected minimized streaming window: {window_copy['service']} - {window_copy['title']}")
        return window_copy
    
    return None

def find_media_image(media_info: Dict) -> Optional[str]:
    """Find media image from TMDB API only."""
    title = media_info.get("title", "").strip()
    media_type = media_info.get("type", "movie")
    
    # Extract the main series title and season info for TV shows
    series_title = title
    season_number = None
    episode_number = None
    
    if media_type == "show":
        # Get the season/episode numbers directly from media_info if available
        season_number = media_info.get("season")
        episode_number = media_info.get("episode")
        
        # Try to extract just the series name using common patterns
        # Pattern 1: "Show Name S1:E1" format
        series_match = re.match(r'^(.*?)\s+S\d+', series_title)
        if series_match:
            series_title = series_match.group(1).strip()
            logger.info(f"Extracted series title: '{series_title}' from '{title}'")
        
        # Pattern 2: "Show Name - S01E01" format
        series_match2 = re.match(r'^(.*?)\s+-\s+S\d+E\d+', series_title)
        if series_match2:
            series_title = series_match2.group(1).strip()
            logger.info(f"Extracted series title: '{series_title}' from '{title}'")
            
        # Pattern 3: "Show Name Season 1 Episode 1" format
        series_match3 = re.match(r'^(.*?)\s+S(?:eason)?\s*\d+\s+E(?:pisode)?\s*\d+', series_title, re.IGNORECASE)
        if series_match3:
            series_title = series_match3.group(1).strip()
            logger.info(f"Extracted series title: '{series_title}' from '{title}'")
    
    # Clean title for searching
    search_title = clean_title(series_title, media_info.get("service", ""))
    
    # Clean title for logging to avoid Unicode errors
    clean_log_title = clean_title_for_logging(search_title)
    
    logger.info(f"Searching for image: '{clean_log_title}'")
    
    # Only use TMDB API for searching
    tmdb_image = find_improved_tmdb_image(search_title, media_type, season_number, episode_number)
    if tmdb_image:
        return tmdb_image
    
    logger.warning(f"No image found for '{clean_log_title}' using TMDB API")
    return None

def find_improved_tmdb_image(title: str, media_type: str, season_number: Optional[int] = None, episode_number: Optional[int] = None) -> Optional[str]:
    """Enhanced TMDB image search with multiple methods."""
    try:
        # Log the exact query we're searching with
        logger.info(f"Searching TMDB for: '{title}' (type: {media_type})")
        
        # Strategy 1: Try to get season-specific poster if applicable
        if media_type == "show" and season_number is not None:
            season_poster = find_season_image_tmdb(title, season_number)
            if season_poster:
                return season_poster
        
        # Strategy 2: Strip out any numbers or special characters that might interfere with the search
        # This helps with titles like "Grey's Anatomy S1 Episode 1" -> "Grey's Anatomy"
        simplified_title = re.sub(r'[^\w\s\']', ' ', title)  # Keep apostrophes for names like "Grey's"
        simplified_title = re.sub(r'\s\d+\s', ' ', simplified_title)  # Remove standalone numbers
        simplified_title = re.sub(r'\s+', ' ', simplified_title).strip()  # Fix multiple spaces
        
        if simplified_title != title:
            logger.info(f"Also trying simplified title: '{simplified_title}'")
        
        # Try searching with both the original and simplified titles
        search_titles = [title]
        if simplified_title != title:
            search_titles.append(simplified_title)
        
        for search_title in search_titles:
            # Try different content types based on media_type
            search_types = []
            if media_type == "show":
                search_types = ["tv", "movie"]  # Try TV first for shows
            else:
                search_types = ["movie", "tv"]  # Try movie first for movies
                
            for search_type in search_types:
                logger.info(f"Trying TMDB search for {search_type}: '{search_title}'")
                
                try:
                    response = requests.get(
                        f"{TMDB_API_BASE}/search/{search_type}",
                        params={
                            "api_key": TMDB_API_KEY,
                            "query": search_title,
                            "include_adult": "false"  # Filter adult content for better matches
                        },
                        timeout=5
                    )
                    
                    if response.status_code != 200:
                        logger.warning(f"TMDB API returned status code {response.status_code}")
                        continue
                    
                    data = response.json()
                    
                    if data.get("results") and len(data["results"]) > 0:
                        # Get all potential matches
                        results = data["results"]
                        
                        # Score results by relevance to our query
                        scored_results = []
                        for result in results:
                            result_title = result.get("title") or result.get("name", "Unknown")
                            score = 0
                            
                            # Exact match gets highest score
                            if result_title.lower() == search_title.lower():
                                score += 100
                            # Title contains our search as a substring
                            elif search_title.lower() in result_title.lower():
                                score += 50
                            # Our search contains the result title
                            elif result_title.lower() in search_title.lower():
                                score += 40
                                
                            # Add popularity as a smaller factor
                            score += result.get("popularity", 0) / 10
                            
                            # Newer content is likely more relevant
                            if result.get("release_date") or result.get("first_air_date"):
                                release_date = result.get("release_date") or result.get("first_air_date")
                                try:
                                    year = int(release_date[:4])
                                    current_year = int(time.strftime("%Y"))
                                    # Newer content gets higher score, max bonus of 20 for current year
                                    year_bonus = min(20, max(0, (year - 2000) / (current_year - 2000) * 20))
                                    score += year_bonus
                                except:
                                    pass
                            
                            # Only add results with poster paths
                            if result.get("poster_path"):
                                scored_results.append((score, result))
                        
                        # Sort by score, highest first
                        scored_results.sort(reverse=True, key=lambda x: x[0])
                        
                        # Log the top matches
                        for score, result in scored_results[:3]:
                            result_title = result.get("title") or result.get("name", "Unknown")
                            logger.info(f"Match: '{result_title}' (score: {score:.1f})")
                        
                        # Use the highest-scoring result with a poster
                        if scored_results:
                            top_result = scored_results[0][1]  # Get actual result from score, result tuple
                            result_title = top_result.get("title") or top_result.get("name", "Unknown")
                            poster_path = top_result.get("poster_path")
                            
                            if poster_path:
                                full_path = f"https://image.tmdb.org/t/p/w200{poster_path}"
                                logger.info(f"Selected poster for '{result_title}': {full_path}")
                                return poster_path
                
                except Exception as e:
                    logger.error(f"TMDB request error: {e}")
                    continue
        
        # If still no match, try with just the first few words
        words = simplified_title.split()
        if len(words) > 2:
            short_title = " ".join(words[:2])
            logger.info(f"Trying with shortened title: '{short_title}'")
            
            # Recursive call with shortened title
            short_result = find_improved_tmdb_image(short_title, media_type, season_number, episode_number)
            if short_result:
                return short_result
        
        # No matches found
        logger.warning(f"No matches found for '{title}'")
        return None
    except Exception as e:
        logger.error(f"Error in improved TMDB image search: {e}")
        return None

def find_season_image_tmdb(series_title: str, season_number: int) -> Optional[str]:
    """Find season-specific poster image from TMDB."""
    try:
        logger.info(f"Searching for series: '{clean_title_for_logging(series_title)}'")
        
        # First, find the TV show by title
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/search/tv",
                params={
                    "api_key": TMDB_API_KEY,
                    "query": series_title
                },
                timeout=5
            )
            
            if response.status_code != 200:
                logger.warning(f"TMDB API returned status code {response.status_code}")
                return None
            
            search_data = response.json()
            
            if not search_data.get("results") or len(search_data["results"]) == 0:
                logger.info(f"No TV series found for '{clean_title_for_logging(series_title)}'")
                return None
                
            # Get the most popular result
            show_id = search_data["results"][0]["id"]
            show_name = search_data["results"][0]["name"]
            logger.info(f"Found TV series: '{show_name}' (ID: {show_id})")
            
            # Now get the season details to find the season poster
            season_response = requests.get(
                f"{TMDB_API_BASE}/tv/{show_id}/season/{season_number}",
                params={"api_key": TMDB_API_KEY},
                timeout=5
            )
            
            if season_response.status_code != 200:
                logger.warning(f"TMDB API returned status code {season_response.status_code} for season lookup")
                return None
                
            season_data = season_response.json()
            
            # Check if season has a poster
            if season_data.get("poster_path"):
                poster_path = season_data["poster_path"]
                logger.info(f"Found season {season_number} poster for '{show_name}'")
                return poster_path
            else:
                logger.info(f"No poster found for season {season_number} of '{show_name}'")
                # Fallback to the series poster
                return search_data["results"][0].get("poster_path")
            
        except Exception as e:
            logger.error(f"Error searching for TV series: {e}")
            return None
            
    except Exception as e:
        logger.error(f"Error in season image search: {e}")
        return None

def update_readme_with_api_info():
    """Add information to the console about using TMDB."""
    logger.info("\n===== TMDB IMAGE SEARCH =====")
    logger.info("Using TMDB (The Movie Database) API for thumbnails")
    logger.info("This is a free service with no API key registration needed for this app")
    logger.info("If thumbnails aren't showing correctly, please check your internet connection")
    logger.info("==============================")

def update_presence(media_info: Dict, image_key: Optional[str] = None):
    """Update Discord rich presence."""
    global start_timestamp, rpc
    
    # Clean up title for display
    display_title = clean_title(media_info["title"], media_info["service"])
        
    # Limit title length to avoid Discord API errors
    if len(display_title) > 100:
        display_title = display_title[:97] + "..."
    
    # Use static image keys that match what you've uploaded to Discord
    large_image_key = "netflix" if media_info["service"] == "Netflix" else "disney"
    
    # Set a default state if none is provided
    if media_info.get("type") == "show" and media_info.get("season") and media_info.get("episode"):
        # Create just 'S1:E1' without any trailing 'on' text
        state = f"S{media_info['season']}:E{media_info['episode']}"
        if media_info.get("episodeTitle"):
            # Clean episode title too
            clean_episode_title = clean_title(media_info['episodeTitle'], media_info["service"])
            # Make sure there's no "on" at the end
            clean_episode_title = re.sub(r'\s+on\s*$', '', clean_episode_title)
            state += f" - {clean_episode_title}"
            # Limit state length
            if len(state) > 100:
                state = state[:97] + "..."
    else:
        # Default state for movies or when episode info is unavailable
        # Keep "Watching on Service" format for movies
        state = f"Watching on {media_info['service']}"
    
    # Create simple activity data first - minimal data reduces chance of timeout
    minimal_activity = {
        "details": display_title,
        "state": state,
        "large_image": large_image_key
    }

    # Update Discord presence using a tiered approach
    update_successful = False
    
    # Try the minimal activity first
    try:
        rpc.update(**minimal_activity)
        logger.info(f"Basic presence update successful: {media_info['service']} - {clean_title_for_logging(display_title)}")
        update_successful = True
    except Exception as e:
        logger.warning(f"Basic presence update failed: {e}")
        
        # Check if it's a timeout error - attempt reconnection first
        if "No response was received from the pipe in time" in str(e):
            logger.warning("Discord pipe timeout detected. Attempting to reconnect...")
            if reconnect_discord():
                # Retry after reconnection
                try:
                    rpc.update(**minimal_activity)
                    logger.info("Presence update successful after reconnection")
                    update_successful = True
                except Exception as retry_error:
                    logger.error(f"Update still failed after reconnection: {retry_error}")
    
    # If the basic update was successful and we have an image, try to add it
    if update_successful and image_key:
        try:
            # Wait a moment before adding the image
            time.sleep(1.0)
            
            # Prepare activity with image
            image_activity = minimal_activity.copy()
            
            # Add small image
            try:
                # Check if it's a full URL or just a path
                if isinstance(image_key, str) and image_key.startswith('http'):
                    image_activity["small_image"] = image_key
                else:
                    image_activity["small_image"] = f"https://image.tmdb.org/t/p/w200{image_key}"
                    
                image_activity["small_text"] = display_title[:100] if len(display_title) > 100 else display_title
                logger.info(f"Using image URL: {image_activity['small_image']}")
                
                # Update with image
                rpc.update(**image_activity)
                logger.info("Presence updated with thumbnail image")
            except Exception as img_err:
                logger.error(f"Error setting thumbnail image: {img_err}")
        except Exception as e:
            logger.error(f"Error updating presence with image: {e}")
    
    # If everything worked well so far, try to add buttons
    if update_successful:
        try:
            # Wait another moment before adding buttons
            time.sleep(1.0)
            
            # Try to build the URL properly
            service_url = ""
            if media_info['service'] == "Netflix":
                service_url = "https://www.netflix.com"
            elif media_info['service'] == "Disney+":
                service_url = "https://www.disneyplus.com"
            
            # Create full activity data
            full_activity = image_activity.copy() if 'image_activity' in locals() else minimal_activity.copy()
            full_activity["large_text"] = f"Watching on {media_info['service']}"
            
            # Only add buttons if supported
            try:
                full_activity["buttons"] = [
                    {"label": f"Watch on {media_info['service']}", "url": service_url}
                ]
                
                rpc.update(**full_activity)
                logger.info("Full presence updated with buttons")
            except Exception as button_err:
                logger.error(f"Error adding buttons to presence: {button_err}")
        except Exception as e:
            logger.error(f"Error updating full presence: {e}")

def reconnect_discord():
    """Reconnect to Discord if connection is lost."""
    global rpc
    
    try:
        # First try to properly close the connection
        try:
            rpc.close()
        except:
            pass
            
        # Wait a longer moment
        time.sleep(3)
        
        # Attempt to reconnect
        logger.info("Reconnecting to Discord...")
        rpc = Presence(DISCORD_CLIENT_ID)
        rpc.connect()
        logger.info("Successfully reconnected to Discord!")
        return True
        
    except Exception as e:
        logger.error(f"Failed to reconnect to Discord: {e}")
        return False

def check_native_apps() -> Optional[Dict[str, Union[str, bool, int]]]:
    """Check if Netflix or Disney+ apps are running and in focus."""
    # Get the active window first for faster checking
    active_window_hwnd = win32gui.GetForegroundWindow()
    active_window_title = win32gui.GetWindowText(active_window_hwnd)
    
    # Store all streaming apps we find
    streaming_apps = []
    
    # Quick check of active window first (faster)
    if NETFLIX_APP_NAME in active_window_title:
        # Analyze title to extract show/movie info
        media_info = parse_netflix_title(active_window_title)
        if media_info:
            media_info["isWatching"] = True
            media_info["service"] = "Netflix"
            media_info["window_hwnd"] = active_window_hwnd
            media_info["is_visible"] = True
            media_info["is_running"] = True
            streaming_apps.append(media_info)
    
    # Check for Disney+ in active window
    if DISNEY_APP_NAME in active_window_title:
        # Analyze title to extract show/movie info
        media_info = parse_disney_title(active_window_title)
        if media_info:
            media_info["isWatching"] = True
            media_info["service"] = "Disney+"
            media_info["window_hwnd"] = active_window_hwnd
            media_info["is_visible"] = True
            media_info["is_running"] = True
            streaming_apps.append(media_info)
    
    # Now check all windows to detect media apps even if not in focus/visible
    windows = get_all_windows()
    
    for hwnd, title, process_name in windows:
        # Skip windows we've already processed (the active one)
        if hwnd == active_window_hwnd:
            continue
            
        if not title:
            continue
            
        # Ignore certain window titles to avoid false positives
        if any(ignored in title.lower() for ignored in [".env", "settings", "cursor", "code editor"]):
            continue
            
        # Check Netflix app
        if NETFLIX_APP_NAME in title:
            # Analyze title to extract show/movie info
            media_info = parse_netflix_title(title)
            if media_info:
                media_info["isWatching"] = True
                media_info["service"] = "Netflix"
                media_info["window_hwnd"] = hwnd
                media_info["is_visible"] = win32gui.IsWindowVisible(hwnd)
                media_info["is_running"] = True
                streaming_apps.append(media_info)
                
        # Check Disney+ app
        if DISNEY_APP_NAME in title:
            # Analyze title to extract show/movie info
            media_info = parse_disney_title(title)
            if media_info:
                media_info["isWatching"] = True
                media_info["service"] = "Disney+"
                media_info["window_hwnd"] = hwnd
                media_info["is_visible"] = win32gui.IsWindowVisible(hwnd)
                media_info["is_running"] = True
                streaming_apps.append(media_info)
    
    # If no media apps found
    if not streaming_apps:
        return None
    
    # IMPORTANT: Modified prioritization logic:
    # 1. First prefer active window if available
    # 2. Otherwise, use any streaming app that's found, even if minimized
    
    for app in streaming_apps:
        if app["window_hwnd"] == active_window_hwnd:
            # Active window gets priority
            result = app.copy()
            if "window_hwnd" in result:
                del result["window_hwnd"]
            if "is_visible" in result:
                del result["is_visible"]
            if "is_running" in result:
                del result["is_running"]
            logger.info(f"Selected active app: {result['service']} - {result['title']}")
            return result
    
    # No active window, just use the first one found (even if minimized)
    result = streaming_apps[0].copy()
    if "window_hwnd" in result:
        del result["window_hwnd"]
    if "is_visible" in result:
        del result["is_visible"]
    if "is_running" in result:
        del result["is_running"]
    logger.info(f"Selected background app: {result['service']} - {result['title']}")
    return result

def check_system_processes() -> Optional[Dict[str, Union[str, bool, int]]]:
    """Check running processes to find Netflix or Disney+ apps even if minimized."""
    logger.debug("Checking system processes for streaming apps")
    
    try:
        all_processes = list(psutil.process_iter(['pid', 'name', 'cmdline']))
        
        # Look for Disney+ processes - checking multiple possible process names
        disney_process_names = ["Disney+.exe", "WWAHost.exe", "ApplicationFrameHost.exe", "explorer.exe"]
        netflix_process_names = ["Netflix.exe", "WWAHost.exe", "ApplicationFrameHost.exe"]

        # First, specifically check for Disney+ as it's causing the issue
        for proc in all_processes:
            try:
                proc_name = proc.info['name'] if proc.info.get('name') else ""
                cmd_line = proc.info.get('cmdline', [])
                cmd_str = " ".join(cmd_line).lower() if cmd_line else ""
                
                # Look for process names that might be hosting Disney+
                if any(proc_name.lower() == disney_proc.lower() for disney_proc in disney_process_names):
                    logger.info(f"Found potential Disney+ process: {proc_name}")
                    
                    # For ApplicationFrameHost or other generic hosts, check command line or window titles
                    if proc_name.lower() in ["applicationframehost.exe", "wwahost.exe", "explorer.exe"]:
                        # Check if Disney+ is in the command line
                        if not any(disney_term in cmd_str.lower() for disney_term in ["disney", "disneyplus"]):
                            # If not in command line, check all windows to see if there's a Disney+ window
                            has_disney_window = False
                            for hwnd, title, _ in get_all_windows():
                                if DISNEY_APP_NAME in title:
                                    has_disney_window = True
                                    logger.info(f"Found Disney+ window: {clean_title_for_logging(title)}")
                                    # Parse the window title for details
                                    media_info = parse_disney_title(title)
                                    if media_info:
                                        media_info["isWatching"] = True
                                        media_info["service"] = "Disney+"
                                        media_info["detected_by_system"] = True  # Mark as detected by system
                                        logger.info(f"Disney+ content detected: {clean_title_for_logging(media_info.get('title', 'Unknown'))}")
                                        return media_info
                                    break
                            
                            if not has_disney_window:
                                # No Disney+ window found for this process
                                continue
                    
                    # If we reached here, we likely have a Disney+ process
                    # Try to find its window
                    window_title = ""
                    for hwnd, title, _ in get_all_windows():
                        if DISNEY_APP_NAME in title:
                            window_title = title
                            break
                    
                    # If we found a window title, parse it for content details
                    if window_title:
                        media_info = parse_disney_title(window_title)
                        if media_info:
                            media_info["isWatching"] = True
                            media_info["service"] = "Disney+"
                            media_info["detected_by_system"] = True  # Mark as detected by system
                            logger.info(f"Disney+ content detected from window: {clean_title_for_logging(media_info.get('title', 'Unknown'))}")
                            return media_info
                    
                    # If we didn't find details but we're sure Disney+ is running, return generic info
                    logger.info("Disney+ process confirmed, returning generic info")
                    return {
                        "isWatching": True,
                        "service": "Disney+",
                        "title": "Disney+ Content",
                        "type": "unknown",
                        "detected_by_system": True  # Mark as detected by system
                    }
            
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                logger.debug(f"Process access error: {str(e)}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error processing Disney+: {str(e)}")
                continue
        
        # Now check for Netflix processes (same approach as Disney+)
        for proc in all_processes:
            try:
                proc_name = proc.info['name'] if proc.info.get('name') else ""
                cmd_line = proc.info.get('cmdline', [])
                cmd_str = " ".join(cmd_line).lower() if cmd_line else ""
                
                if any(proc_name.lower() == netflix_proc.lower() for netflix_proc in netflix_process_names):
                    logger.info(f"Found potential Netflix process: {proc_name}")
                    
                    # For generic hosts, check command line or window titles
                    if proc_name.lower() in ["applicationframehost.exe", "wwahost.exe"]:
                        if not any(netflix_term in cmd_str.lower() for netflix_term in ["netflix"]):
                            # Check windows for Netflix titles
                            has_netflix_window = False
                            for hwnd, title, _ in get_all_windows():
                                if NETFLIX_APP_NAME in title:
                                    has_netflix_window = True
                                    media_info = parse_netflix_title(title)
                                    if media_info:
                                        media_info["isWatching"] = True
                                        media_info["service"] = "Netflix"
                                        media_info["detected_by_system"] = True
                                        return media_info
                                    break
                                    
                            if not has_netflix_window:
                                continue
                    
                    # Try to find Netflix window
                    window_title = ""
                    for hwnd, title, _ in get_all_windows():
                        if NETFLIX_APP_NAME in title:
                            window_title = title
                            break
                    
                    if window_title:
                        media_info = parse_netflix_title(window_title)
                        if media_info:
                            media_info["isWatching"] = True
                            media_info["service"] = "Netflix"
                            media_info["detected_by_system"] = True
                            return media_info
                    
                    # Generic Netflix info
                    return {
                        "isWatching": True,
                        "service": "Netflix",
                        "title": "Netflix Content",
                        "type": "unknown",
                        "detected_by_system": True
                    }
            
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as e:
                logger.error(f"Unexpected error processing Netflix: {str(e)}")
                continue

        # Additional check for browser tabs with "Netflix" or "Disney+" in the title
        # that also contain "and x more pages" which indicates multiple browser tabs
        for hwnd, title, process_name in get_all_windows():
            try:
                if not title:
                    continue
                    
                # Safe logging
                safe_title = clean_title_for_logging(title)
                
                # Check for browser patterns that indicate streaming in a tab
                if (("Netflix" in title or "Disney+" in title) and 
                    any(pattern in title for pattern in ["more pages", "more tab", "and tab"])):
                    
                    # This is likely a browser with multiple tabs, one of which has streaming content
                    # But we need to filter out specific false positives (readme files, etc)
                    if any(pattern in title.lower() for pattern in [
                        "readme", ".md", "documentation", "github", 
                        "developer", "programming", "code", 
                        "disney + & netflix"  # This is your project name
                    ]):
                        logger.info(f"Skipping false positive browser tab: {safe_title}")
                        continue
                        
                    if "Netflix" in title:
                        logger.info(f"Found browser with Netflix tab: {safe_title}")
                        return {
                            "isWatching": True,
                            "service": "Netflix",
                            "title": "Netflix Content",
                            "type": "unknown",
                            "detected_by_system": True
                        }
                    elif "Disney+" in title:
                        logger.info(f"Found browser with Disney+ tab: {safe_title}")
                        return {
                            "isWatching": True,
                            "service": "Disney+",
                            "title": "Disney+ Content",
                            "type": "unknown",
                            "detected_by_system": True
                        }
            except Exception as e:
                logger.error(f"Error checking browser tab: {str(e)}")
                continue
        
        # No streaming apps found
        return None
        
    except Exception as e:
        logger.error(f"Error checking system processes: {str(e)}")
        return None

def detect_media():
    """Detect media being watched."""
    global current_media, start_timestamp, rpc
    
    logger.debug("Checking for media...")
    
    # CHANGE ORDER - start with system processes check first which is most reliable
    # First check system processes (most thorough)
    media_info = check_system_processes()
    
    # If not found in system processes, check native apps
    if not media_info:
        media_info = check_native_apps()
    
    # If still not found, check browsers
    if not media_info:
        media_info = check_browser_tabs()
    
    # If still not found, return not watching
    if not media_info:
        media_info = {"isWatching": False}
    
    # NEW: Super aggressive filtering for readme.md and documentation
    if media_info.get("isWatching", False):
        title = media_info.get("title", "").lower()
        service = media_info.get("service", "")
        
        # Specifically check for readme.md in the title
        if "readme" in title or ".md" in title or "documentation" in title:
            logger.info(f"Detected documentation file ({title}), not a valid streaming media. Clearing presence.")
            media_info = {"isWatching": False}
        
        # Also check for repo names and project folders that might contain "netflix" or "disney"
        elif any(term in title for term in ["repo", "repository", "project", "folder", "file", "code", "github"]):
            logger.info(f"Detected development-related content ({title}), not streaming media. Clearing presence.")
            media_info = {"isWatching": False}
            
        # Check for our specific "readme.md - Disney + & Netflix" document
        elif "disney + & netflix" in title or "streaming" in title:
            logger.info(f"Detected project documentation ({title}), not streaming media. Clearing presence.")
            media_info = {"isWatching": False}
            
        # Filter out titles that look like program names
        elif len(title) < 5 and not media_info.get("detected_by_system", False):
            logger.info(f"Title '{title}' too short, likely not valid media. Clearing presence.")
            media_info = {"isWatching": False}
    
    # If not watching anything, clear presence
    # IMPORTANT: Only clear if we've confirmed no media is playing anywhere
    if not media_info.get("isWatching", False):
        if current_media:
            logger.info("No longer watching media, clearing presence")
            current_media = None
            try:
                # Only try to clear if Discord is connected
                if 'rpc' in globals() and rpc:
                    rpc.clear()
                    logger.info("Discord presence cleared successfully")
            except Exception as e:
                logger.error(f"Error clearing presence: {e}")
        return
    
    # Filter out false positives - ONLY exclude if not from our system_processes check
    # If it was detected by our system process check, trust it completely
    title = media_info.get("title", "").lower()
    detected_by_system = media_info.get("detected_by_system", False)
    
    # Skip false positive check if detected by system check
    if not detected_by_system:
        # Improved false positive list with more patterns
        ignore_patterns = [
            ".env", "settings", "config", "cursor", "visual studio", "vscode", 
            "code editor", "discord", "settings.json", "explorer", "file", "folder",
            "cmd", "command", "powershell", "terminal", "python", "readme", "github",
            ".md", "markdown", "documentation", "notepad", "editor", "setting", 
            "preference", "profile", "account", "login", "sign in", "guide", "tutorial",
            "help", "support", "download", "upload", "install", "setup", "configure",
            "json", "xml", "yaml", "ini", "conf", "log", "txt", "text", "document",
            "license", "copyright", "about", "information", "readme.md", "cursor",
            "disney + & netflix", "streaming", "presence", "rich presence", "discord presence"
        ]
        
        # Skip if the title contains any ignored patterns
        if any(pattern.lower() in title for pattern in ignore_patterns):
            logger.info(f"Ignoring false positive: {title}")
            if current_media:
                logger.info("Cleared presence due to false positive detection")
                current_media = None
                try:
                    # Only try to clear if Discord is connected
                    if 'rpc' in globals() and rpc:
                        rpc.clear()
                        logger.info("Discord presence cleared successfully")
                except Exception as e:
                    logger.error(f"Error clearing presence: {e}")
            return
    else:
        logger.info("Media detected by system process check, bypassing false positive filter")
    
    # Check if the service has changed which requires reconnecting with a different client ID
    service_changed = (current_media and 
                      current_media.get("service") != media_info.get("service"))
    
    # Track time since last update to force periodic updates
    current_time = int(time.time())
    force_update = False
    
    # Force an update every 3 minutes (180 seconds) even if media hasn't changed
    # This helps ensure Discord presence stays visible
    if start_timestamp and (current_time - start_timestamp) % 180 < 5:
        force_update = True
        logger.info("Forcing presence update to keep status visible")
    
    # If media has changed or this is a new session, update presence
    if (not current_media or 
        media_info["service"] != current_media["service"] or 
        media_info["title"] != current_media["title"] or 
        media_info.get("episode") != current_media.get("episode") or
        media_info.get("season") != current_media.get("season") or
        force_update):
        
        logger.info(f"Media changed or update forced: {media_info['service']} - {media_info['title']}")
        
        # Only reset timestamp if media actually changed (not on forced updates)
        if not force_update or not start_timestamp:
            start_timestamp = current_time
        
        # If the service has changed or we're not connected, we need to reconnect with the appropriate client ID
        if service_changed or not ('rpc' in globals() and rpc):
            # Close existing connection if there is one
            if 'rpc' in globals() and rpc:
                try:
                    rpc.clear()
                    rpc.close()
                    logger.info("Closed existing Discord connection")
                except Exception as e:
                    logger.error(f"Error closing Discord connection: {e}")
            
            # Select the appropriate client ID based on the service
            client_id = DISCORD_CLIENT_ID
            if media_info["service"] == "Disney+":
                client_id = DISNEY_CLIENT_ID
                logger.info("Using Disney+ client ID for Discord connection")
            elif media_info["service"] == "Netflix":
                client_id = NETFLIX_CLIENT_ID
                logger.info("Using Netflix client ID for Discord connection")
            
            # Connect with the selected client ID
            try:
                rpc = Presence(client_id)
                rpc.connect()
                logger.info(f"Connected to Discord with {media_info['service']} client ID")
            except Exception as e:
                logger.error(f"Failed to connect to Discord: {e}")
        
        current_media = media_info.copy()  # Create a copy to avoid reference issues
        
        # Don't try to update Discord if it's not connected
        if not ('rpc' in globals() and rpc):
            logger.info("Media detected but Discord is not connected - skipping presence update")
            return
            
        # Find image for the content - do this in a more efficient way
        image_key = find_media_image(media_info)
        
        # Update Discord rich presence
        update_presence(media_info, image_key)
    else:
        logger.debug(f"Still watching: {current_media['service']} - {current_media['title']}")

def main():
    """Main function to run the Discord Rich Presence."""
    logger.info("Starting Discord Rich Presence for Egbot & Chill 🍕🍿")
    
    # Check if environment variables are set
    if not DISCORD_CLIENT_ID:
        logger.error("DISCORD_CLIENT_ID not set in .env file")
        logger.error("Create a .env file with your Discord Client ID and TMDB API key")
        logger.error("See readme.md for instructions")
        sys.exit(1)
    
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not set in .env file")
        logger.error("Create a .env file with your Discord Client ID and TMDB API key")
        logger.error("See readme.md for instructions")
        sys.exit(1)
        
    # Display available and missing API keys to help user enhance thumbnails
    update_readme_with_api_info()

    # Initialize Discord RPC - this might fail but we'll continue running
    discord_connected = connect_to_discord()
    if not discord_connected:
        logger.warning("Starting in offline mode: Media detection active but Discord status won't update")
    
    # Main loop
    try:
        # Initial detection frequency variables
        current_interval = 5  # Start with a 5-second interval
        consecutive_no_media = 0
        consecutive_errors = 0
        
        while True:
            try:
                detect_media()
                consecutive_errors = 0  # Reset error counter on success
                
                # Adaptive detection frequency
                if current_media:
                    # When watching something, check every 5 seconds
                    consecutive_no_media = 0
                    current_interval = 5
                else:
                    # When not watching, gradually increase the interval
                    consecutive_no_media += 1
                    if consecutive_no_media > 5:  # After 5 checks with no media
                        current_interval = 15  # Check every 15 seconds
                    if consecutive_no_media > 20:  # After 20 checks
                        current_interval = 30  # Check every 30 seconds
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in detection cycle: {e}")
                
                # If we have multiple consecutive errors and Discord was previously connected, try reconnecting
                if consecutive_errors >= 3 and discord_connected:
                    logger.warning("Multiple consecutive errors detected. Attempting to reconnect...")
                    discord_connected = reconnect_discord()
                    consecutive_errors = 0  # Reset counter after reconnect attempt
            
            time.sleep(current_interval)
            
    except KeyboardInterrupt:
        logger.info("Shutting down due to keyboard interrupt...")
    except Exception as e:
        logger.error(f"Error in main loop: {e}")
    finally:
        # Let the atexit handler handle cleanup
        pass

def connect_to_discord():
    """Connect to Discord with retry logic."""
    global rpc
    
    max_retries = 5
    retry_count = 0
    
    # Check if Discord is running first
    discord_running = False
    for proc in psutil.process_iter(['name']):
        if 'discord' in proc.info['name'].lower():
            discord_running = True
            break
    
    if not discord_running:
        logger.error("Discord does not appear to be running. Please start Discord and try again.")
        logger.info("The application will keep running but won't update your Discord status")
        return False
        
    while retry_count < max_retries:
        try:
            logger.info(f"Attempting to connect to Discord with Client ID: {DISCORD_CLIENT_ID}")
            rpc = Presence(DISCORD_CLIENT_ID)
            # Set a timeout for connection
            rpc.connect()
            logger.info("Discord RPC connected successfully!")
            return True
        except Exception as e:
            retry_count += 1
            error_message = str(e)
            
            # More user-friendly error message
            if "create_pipe_connection" in error_message:
                logger.error("Error: Discord RPC connection failed due to event loop incompatibility")
                logger.info("This is usually caused by a conflict with asyncio or pypresence")
                
                # Try alternative approach on next retry
                if retry_count < max_retries:
                    logger.info("Trying alternative connection method...")
                    try:
                        # Directly use the Discord pipe
                        rpc = Presence(DISCORD_CLIENT_ID, pipe=0)
                        rpc.connect()
                        logger.info("Discord RPC connected successfully using alternative method!")
                        return True
                    except Exception as alt_e:
                        logger.error(f"Alternative connection also failed: {alt_e}")
            else:
                logger.error(f"Failed to connect to Discord (attempt {retry_count}/{max_retries}): {error_message}")
            
            if retry_count < max_retries:
                wait_time = 2 ** retry_count  # Exponential backoff
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error("Maximum retry attempts reached.")
                logger.error("The application will continue running but won't update your Discord status.")
                logger.error("Please make sure Discord is running and your Client ID is correct.")
                logger.error("Make sure you've set up the separate Client IDs for Disney+ and Netflix.")
                logger.error(f"Current Client IDs:")
                logger.error(f"Default: {DISCORD_CLIENT_ID}")
                logger.error(f"Disney+: {DISNEY_CLIENT_ID}")
                logger.error(f"Netflix: {NETFLIX_CLIENT_ID}")
                return False

def safe_cleanup():
    """Safe cleanup function for when program exits."""
    logger.info("Performing safe cleanup...")
    try:
        if 'rpc' in globals() and rpc:
            logger.info("Closing Discord connection...")
            try:
                rpc.clear()
                time.sleep(0.5)
                rpc.close()
            except Exception as e:
                logger.error(f"Error during Discord cleanup: {e}")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    logger.info("Cleanup complete. Exiting.")

# Register the cleanup function
atexit.register(safe_cleanup)

if __name__ == "__main__":
    try:
        # Check for test mode
        if len(sys.argv) > 1 and sys.argv[1] == "--test":
            logger.info("Running in TEST MODE")
            # Initialize Discord RPC
            try:
                rpc = Presence(DISCORD_CLIENT_ID)
                rpc.connect()
                logger.info("Discord RPC connected successfully!")
                
                # Test presence with Netflix
                test_media = {
                    "isWatching": True,
                    "service": "Netflix",
                    "title": "Test Show",
                    "type": "show",
                    "season": 1,
                    "episode": 1
                }
                
                start_timestamp = int(time.time())
                update_presence(test_media)
                
                logger.info("Test presence sent! Check your Discord status.")
                logger.info("Press Ctrl+C to exit.")
                
                # Keep the script running
                while True:
                    time.sleep(10)
                    
            except Exception as e:
                logger.error(f"Test mode error: {e}")
        else:
            main()
    except KeyboardInterrupt:
        logger.info("Application terminated by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
    # No finally block needed - atexit will handle cleanup 