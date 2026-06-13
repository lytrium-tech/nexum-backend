
for s in ['smoke_pending_actions_lifecycle.py', 'smoke_clarification.py']:
    path = f'scripts/{s}'
    with open(path, encoding='utf-8') as f:
        content = f.read()
    
    content = content.replace('\u2705', '[OK]').replace('🎉', '')
    content = content.replace('simulate_message(client, "Nequi")', 'simulate_message(client, "hola")')
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
