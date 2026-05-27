content = open('cdp_worker.py', encoding='utf-8').read()
old_frag = "sessionid=;|sessionid=\\\\s*;/)"
new_frag = "sessionid=;')"
idx = content.find(old_frag)
if idx >= 0:
    # Replace the whole match line
    line_start = content.rfind('\n', 0, idx) + 1
    line_end = content.find('\n', idx)
    old_line = content[line_start:line_end]
    new_line = "                    !document.cookie.includes('sessionid=;'))"
    content = content[:line_start] + new_line + content[line_end:]
    open('cdp_worker.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Pattern not found')
