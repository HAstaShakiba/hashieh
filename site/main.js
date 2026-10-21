// Shared by the English and Persian pages.
const fa = document.documentElement.lang === "fa";
const t = fa ? {
  windows: "دانلود برای ویندوز", mac: "دانلود برای مک", linux: "دانلود برای اوبونتو / دبیان",
  version: "نسخه‌ی", license: "مجوز MIT", mb: "مگابایت", kb: "کیلوبایت",
  copy: "کپی", copied: "کپی شد", copyFail: "انتخاب و کپی کنید",
} : {
  windows: "Download for Windows", mac: "Download for macOS", linux: "Download for Ubuntu / Debian",
  version: "Version", license: "MIT license", mb: "MB", kb: "KB",
  copy: "Copy", copied: "Copied", copyFail: "Select & copy",
};

// Point the download button at the newest file for this visitor's system.
const ua = navigator.userAgent;
const os = /Windows/i.test(ua) ? "windows" : /Mac OS X|Macintosh/i.test(ua) ? "mac"
         : /Linux|X11/i.test(ua) && !/Android/i.test(ua) ? "linux" : null;
const pick = {
  windows: { test: n => n.endsWith("-setup.exe"), label: t.windows },
  mac: { test: n => n.endsWith(".dmg"), label: t.mac },
  linux: { test: n => n.endsWith(".deb"), label: t.linux },
}[os];
if (pick) document.getElementById("download-label").textContent = pick.label;
if (os) document.querySelector(`.card.dl[data-os="${os}"]`)?.classList.add("current");
(async () => {
  try {
    const res = await fetch("https://api.github.com/repos/HAstaShakiba/hashieh/releases/latest");
    if (!res.ok) return;
    const release = await res.json();
    const assets = release.assets || [];
    // Keep every per-OS link pointing at the newest release.
    for (const link of document.querySelectorAll("[data-suffix]")) {
      const asset = assets.find(a => a.name.endsWith(link.dataset.suffix));
      if (asset) link.href = asset.browser_download_url;
    }
    for (const code of document.querySelectorAll("[data-name-for]")) {
      const asset = assets.find(a => a.name.endsWith(code.dataset.nameFor));
      if (asset) code.textContent = asset.name;
    }
    for (const el of document.querySelectorAll(".dl-version")) el.textContent = release.tag_name.replace(/^v/, "");
    if (!pick) return;
    const asset = assets.find(a => pick.test(a.name));
    if (asset) {
      document.getElementById("download").href = asset.browser_download_url;
      const size = asset.size > 1048576 ? `${Math.round(asset.size / 1048576)} ${t.mb}`
                                       : `${Math.round(asset.size / 1024)} ${t.kb}`;
      document.getElementById("release-meta").textContent =
        `${t.version} ${release.tag_name.replace(/^v/, "")} · ${size} · ${t.license}`;
    }
  } catch (e) { /* keep the link to the releases page */ }
})();

// Copy buttons on code blocks.
for (const pre of document.querySelectorAll("pre")) {
  const button = document.createElement("button");
  button.className = "copy";
  button.textContent = t.copy;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(pre.querySelector("code").innerText.trim());
      button.textContent = t.copied;
      setTimeout(() => (button.textContent = t.copy), 1500);
    } catch (e) { button.textContent = t.copyFail; }
  });
  pre.appendChild(button);
}
