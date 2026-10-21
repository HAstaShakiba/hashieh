import os

# Keep tests away from the user's real GNOME settings (shortcuts write to GSettings).
os.environ["GSETTINGS_BACKEND"] = "memory"
