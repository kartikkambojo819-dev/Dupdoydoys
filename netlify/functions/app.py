import os
import re
from flask import Flask, request, Response
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

try:
    import serverless_wsgi
except ImportError:
    serverless_wsgi = None

app = Flask(__name__)

TARGET_SITE = "https://xmovies8.bond/"
TARGET_DOMAIN = urlparse(TARGET_SITE).netloc

scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'android', 'mobile': True}
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
    'Referer': TARGET_SITE,
    'Origin': TARGET_SITE,
}

ANTI_REDIRECT_JS = """
<script>
    window.open = function() { return null; };
    window.onbeforeunload = null;
    document.addEventListener('click', function(e) {
        let target = e.target.closest('a');
        if (target && target.href) {
            try {
                let url = new URL(target.href, window.location.origin);
                if (url.hostname !== window.location.hostname) {
                    e.preventDefault();
                    e.stopPropagation();
                }
            } catch(err) {}
        }
    }, true);
</script>
"""

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def proxy(path):
    target_url = urljoin(TARGET_SITE, path)
    if request.query_string:
        target_url += f"?{request.query_string.decode('utf-8')}"

    try:
        response = scraper.get(target_url, headers=HEADERS, timeout=15)
        
        if response.status_code != 200:
            return f"Error loading resource: {response.status_code}", response.status_code

        content_type = response.headers.get('Content-Type', '')

        # Static Assets (CSS, JS, Fonts, Images) Handling
        if 'text/html' not in content_type:
            res = Response(response.content, content_type=content_type)
            res.headers['Access-Control-Allow-Origin'] = '*'
            res.headers['Cache-Control'] = 'public, max-age=86400'
            return res

        soup = BeautifulSoup(response.text, 'html.parser')

        # 1. Base Tag for direct asset reference fallbacks
        base_tag = soup.new_tag('base', href=f"http://{request.host}/")
        if soup.head:
            soup.head.insert(0, base_tag)

        # 2. Re-route Link/Script/Img tags via local proxy
        for tag, attr in [('a', 'href'), ('link', 'href'), ('script', 'src'), ('img', 'src')]:
            for element in soup.find_all(tag, {attr: True}):
                val = element[attr].strip()
                if val.startswith(('javascript:', 'data:', '#', 'mailto:')):
                    continue
                full_url = urljoin(target_url, val)
                parsed = urlparse(full_url)
                if parsed.netloc == TARGET_DOMAIN:
                    local_path = parsed.path
                    if parsed.query:
                        local_path += f"?{parsed.query}"
                    element[attr] = local_path if local_path.startswith('/') else '/' + local_path

        # 3. Inject Anti-Redirect Script
        final_html = str(soup)
        if '<head>' in final_html:
            final_html = final_html.replace('<head>', f'<head>{ANTI_REDIRECT_JS}', 1)

        return Response(final_html, mimetype='text/html')

    except Exception as e:
        return f"Proxy Error: {str(e)}", 500

def handler(event, context):
    if serverless_wsgi:
        return serverless_wsgi.handle_request(app, event, context)
    return {"statusCode": 500, "body": "serverless_wsgi not installed"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
