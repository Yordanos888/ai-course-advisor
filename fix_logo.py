
import re
with open('templates/base.html', 'r', encoding='utf-8') as f:
    content = f.read()

new_brand = '''<a class=\"navbar-brand d-flex align-items-center\" href=\"/\">
                <img src=\"{{ url_for('static', filename='images/aastu_logo.jpg') }}\" alt=\"AASTU Logo\" width=\"32\" height=\"32\" class=\"me-2 rounded\">
                AASTU ECE Academic Panel
            </a>'''

content = re.sub(r'<a class=\"navbar-brand\".*?</a>', new_brand, content, flags=re.DOTALL)

with open('templates/base.html', 'w', encoding='utf-8') as f:
    f.write(content)

