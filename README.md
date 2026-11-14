# clipes-bot

Robô que varre os clipes mais populares de uma categoria da Twitch a cada 12 h,
separa por idioma (português / inglês, ouvindo o áudio quando a live não marca o idioma),
lembra o que já foi usado e monta o banco de clipes para as compilações.

- `config.json`: categoria e limites (trocar `game_name` para "Grand Theft Auto VI" no lançamento).
- `state/`: memória (clipes usados, idioma de cada streamer, lista de exclusão, bancos por idioma).
- `reports/latest.json`: resultado da última varredura.
- `state/blocklist.json`: streamers que pediram para não aparecer.
