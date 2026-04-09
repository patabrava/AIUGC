import cairosvg

svg_code = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="1000" height="1000">
    <rect width="64" height="64" rx="16" fill="#006AAB"/>
    <path d="M14 44V20h8v16h16v8H14zm18 0V20h8v16h10v8H32z" fill="white"/>
</svg>"""

cairosvg.svg2png(bytestring=svg_code.encode('utf-8'), write_to='static/images/app-logo-1000x1000.png', output_width=1000, output_height=1000)
print("Done")
