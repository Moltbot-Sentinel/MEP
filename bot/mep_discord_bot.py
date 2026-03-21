from clients.adapters.mep_discord_adapter import DISCORD_TOKEN, bot


if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
