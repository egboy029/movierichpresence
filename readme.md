# Discord Rich Presence for Disney+ & Netflix

![Discord Rich Presence Demo](https://i.imgur.com/YZ3tZW3.png)

A Windows application that automatically detects when you're watching Netflix or Disney+ content and displays it as your Discord Rich Presence status, complete with thumbnails, titles, and episode information.

## ✨ Features

- **Automatic Detection**: No manual input required - works with both web browsers and Windows apps
- **Real-time Status**: Shows what you're watching with title, service, and episode information
- **Content Thumbnails**: Displays movie/show thumbnails from TMDB API
- **Smart Recognition**: Detects and parses season and episode information
- **Minimal Resource Usage**: Lightweight background process

## 🔧 Requirements

- Windows 10/11
- Python 3.7+
- Discord desktop app
- Discord Application (for Client ID)
- TMDB API Key (free)

## 📋 Setup Guide

### 1. Create a Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and name it (e.g., "Media Watcher")
3. Go to "Rich Presence" > "Art Assets" 
4. Upload these images:
   - An image named `netflix` for Netflix
   - An image named `disney` for Disney+
5. Copy your "Application ID" from the General Information page

### 2. Get a TMDB API Key

1. Create an account on [The Movie Database](https://www.themoviedb.org/)
2. Go to your account settings > API and request a new API key
3. Select "Developer" usage type
4. Copy your API key (v3 auth)

### 3. Install & Configure

```bash
# Clone this repository
git clone https://github.com/yourusername/discord-streaming-presence
cd discord-streaming-presence

# Install dependencies
pip install -r requirements.txt

# Create your .env file with your API keys
# (or copy .env.example and edit it)
```

Create a `.env` file with:
```
DISCORD_CLIENT_ID=your_discord_application_id
TMDB_API_KEY=your_tmdb_api_key
```

### Separate Discord Application Names (Optional)

For a more integrated experience, you can create separate Discord applications that will display differently:

1. Create two separate Discord applications named "Disney+" and "Netflix":
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create an application named "Disney+"
   - Create another application named "Netflix"
   - For each app, upload their respective logos

2. Update your `.env` file with all the application IDs:
```
DISCORD_CLIENT_ID=your_default_application_id
DISNEY_CLIENT_ID=your_disney_plus_application_id
NETFLIX_CLIENT_ID=your_netflix_application_id
TMDB_API_KEY=your_tmdb_api_key
```

With this setup, when you watch Disney+ content, Discord will show "Disney+" as the app name, and when you watch Netflix, it will show "Netflix" as the app name.

### 4. Run the Application

Either:
- Run `python discord_presence.py`
- Or double-click the included `start.bat` file

## 🚀 Auto-start with Windows

1. Create a shortcut to `start.bat` in the project folder
2. Press `Win+R`, type `shell:startup`, and press Enter
3. Move the shortcut to this folder

## 🔍 How It Works

1. **Detection**: The app continuously scans for Netflix or Disney+ content in:
   - Native Windows app windows
   - Browser tabs in Chrome, Firefox, Edge, or Brave
   
2. **Content Parsing**: When detected, the app extracts:
   - Title of the content
   - Season and episode information (for TV shows)
   - Service (Netflix or Disney+)
   
3. **Image Lookup**: The TMDB API is used to find matching thumbnails

4. **Discord Update**: Your Discord status is updated with the extracted information

## 🔧 Troubleshooting

- **Discord not detecting**: Make sure Discord is running before starting the app
- **Images not showing**: Verify your TMDB API key is valid and that you've uploaded images named "netflix" and "disney" to your Discord application
- **Content not detected**: Check `discord_presence.log` for error messages

## 🔒 Privacy

This application runs entirely locally on your machine. Your watching data is only sent to:
- TMDB (for image lookups)
- Discord (for status updates)

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 📊 Development

Contributions welcome! Feel free to submit issues or pull requests.