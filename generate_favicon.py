from playwright.sync_api import sync_playwright

html_content = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body, html { margin: 0; padding: 0; width: 1000px; height: 1000px; display: block; background: transparent; }
        svg { width: 1000px; height: 1000px; display: block; }
    </style>
</head>
<body>
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
    <rect width='64' height='64' rx='16' fill='#006AAB'/>
    <path d='M14 44V20h8v16h16v8H14zm18 0V20h8v16h10v8H32z' fill='white'/>
</svg>
</body>
</html>
"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1000, 'height': 1000})
    page.set_content(html_content)
    page.locator('svg').screenshot(path='static/images/app-logo.png', omit_background=True)
    browser.close()
print("Done")
