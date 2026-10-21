#!/usr/bin/env bash
# Per-user install from a source checkout: ~/.local/bin/hashieh, a menu entry and an icon,
# and optionally autostart and a GNOME global shortcut that toggles the panel.
#
#   ./install.sh                         install launcher + menu entry
#   ./install.sh --autostart             also start the panel (hidden) at login
#   ./install.sh --shortcut '<Super>n'   also bind a GNOME shortcut to `hashieh --toggle`
#   ./install.sh --uninstall
#
# Shortcuts can also be changed later in the app: ⚙ ▸ Keyboard shortcuts…
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
app_id="io.github.hastashakiba.Hashieh"
bin_dir="$HOME/.local/bin"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
apps_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/scalable/apps"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

autostart=0
shortcut=""
uninstall=0
while [ $# -gt 0 ]; do
    case "$1" in
        --autostart) autostart=1 ;;
        --shortcut) shortcut="$2"; shift ;;
        --uninstall) uninstall=1 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done


# Files from before the rename to Hashieh.
rm -f "$bin_dir/sidenotes" "$apps_dir/io.github.sidenotes_linux.desktop" \
      "$autostart_dir/io.github.sidenotes_linux.desktop"

if [ "$uninstall" = 1 ]; then
    rm -f "$bin_dir/hashieh" "$apps_dir/$app_id.desktop" "$autostart_dir/$app_id.desktop" \
          "$icon_dir/$app_id.svg"
    "$here/bin/hashieh" shortcut toggle "" >/dev/null 2>&1 || true
    "$here/bin/hashieh" shortcut new_note "" >/dev/null 2>&1 || true
    echo "Uninstalled. Notes are kept in $data_home/hashieh."
    exit 0
fi

mkdir -p "$bin_dir" "$apps_dir" "$icon_dir"
ln -sf "$here/bin/hashieh" "$bin_dir/hashieh"
install -m644 "$here/data/icons/$app_id.svg" "$icon_dir/$app_id.svg"
sed "s|@EXEC@|$bin_dir/hashieh|g" "$here/data/$app_id.desktop.in" > "$apps_dir/$app_id.desktop"
echo "Installed $bin_dir/hashieh and $apps_dir/$app_id.desktop"

if [ "$autostart" = 1 ]; then
    mkdir -p "$autostart_dir"
    cat > "$autostart_dir/$app_id.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Hashieh
Comment=Start Hashieh hidden at login
Exec=$bin_dir/hashieh --hide
Icon=$app_id
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
    echo "Enabled autostart"
fi

if [ -n "$shortcut" ]; then
    "$here/bin/hashieh" shortcut toggle "$shortcut"
fi
