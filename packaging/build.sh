#!/usr/bin/env bash
# Build release artifacts into dist/:
#   hashieh-VERSION.tar.gz     source archive (run from source or ./install.sh)
#   hashieh_VERSION_all.deb    Debian/Ubuntu package
set -euo pipefail

root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$root/hashieh/__init__.py")"
app_id="io.github.hastashakiba.Hashieh"
dist="$root/dist"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

rm -rf "$dist"
mkdir -p "$dist"

files=(LICENSE README.md install.sh pyproject.toml bin data hashieh packaging tests)

# Source archive ---------------------------------------------------------------------------
src="$work/hashieh-$version"
mkdir -p "$src"
cp -r "${files[@]/#/$root/}" "$src/"
find "$src" -name __pycache__ -prune -exec rm -rf {} +
tar -C "$work" --owner=0 --group=0 --sort=name -czf "$dist/hashieh-$version.tar.gz" "hashieh-$version"

# Debian package ---------------------------------------------------------------------------
pkg="$work/deb"
install -d "$pkg/DEBIAN" "$pkg/usr/bin" "$pkg/usr/lib/hashieh" "$pkg/usr/share/applications" \
    "$pkg/usr/share/icons/hicolor/scalable/apps" "$pkg/usr/share/doc/hashieh"
cp -r "$root/hashieh" "$pkg/usr/lib/hashieh/"
find "$pkg" -name __pycache__ -prune -exec rm -rf {} +
cat > "$pkg/usr/bin/hashieh" <<'EOF'
#!/bin/sh
PYTHONPATH=/usr/lib/hashieh${PYTHONPATH:+:$PYTHONPATH} exec /usr/bin/python3 -m hashieh "$@"
EOF
chmod 755 "$pkg/usr/bin/hashieh"
sed "s|@EXEC@|hashieh|g" "$root/data/$app_id.desktop.in" > "$pkg/usr/share/applications/$app_id.desktop"
install -m644 "$root/data/icons/$app_id.svg" "$pkg/usr/share/icons/hicolor/scalable/apps/$app_id.svg"
install -m644 "$root/README.md" "$pkg/usr/share/doc/hashieh/README.md"
install -m644 "$root/LICENSE" "$pkg/usr/share/doc/hashieh/copyright"
size="$(du -sk "$pkg/usr" | cut -f1)"
cat > "$pkg/DEBIAN/control" <<EOF
Package: hashieh
Version: $version
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-gi, python3-gi-cairo, python3-cairo, gir1.2-gtk-3.0
Installed-Size: $size
Maintainer: HAstaShakiba <HAstaShakiba@users.noreply.github.com>
Homepage: https://github.com/HAstaShakiba/hashieh
Description: notes panel on the edge of your screen
 Hashieh is a side-panel notes app for Linux: folders, colored note cards,
 light Markdown, tasks, pictures and themes. It can import and export the
 data and themes of SideNotes for macOS. Unofficial; not affiliated with
 Apptorium.
EOF
cat > "$pkg/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
EOF
chmod 755 "$pkg/DEBIAN/postinst"
chmod -R u=rwX,go=rX "$pkg/usr"
dpkg-deb --root-owner-group --build "$pkg" "$dist/hashieh_${version}_all.deb" >/dev/null

(cd "$dist" && sha256sum ./* > SHA256SUMS)
ls -l "$dist"
