importScripts('config.js');

async function applyCookies() {
    const expires = Math.floor(Date.now() / 1000) + 365 * 24 * 60 * 60;
    for (const domain of ['115.com', 'anxia.com']) {
        for (const [name, value] of Object.entries(COOKIE_VALUES)) {
            if (!value) continue;
            try {
                await chrome.cookies.set({
                    url: `https://${domain}/`, domain: `.${domain}`,
                    name, value, path: '/', secure: true, expirationDate: expires
                });
            } catch (error) {
                console.error(`Could not apply ${name} to ${domain}: ${error.message}`);
            }
        }
    }
}

chrome.runtime.onInstalled.addListener(() => { void applyCookies(); });
chrome.runtime.onStartup.addListener(() => { void applyCookies(); });
