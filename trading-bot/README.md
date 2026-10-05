# Trading Bot

Folder untuk bot trading crypto (CEX seperti Bitget/Binance via `ccxt`, atau on-chain Solana).

Status: kerangka awal, belum ada kode bot.

## Aturan keamanan

- Jangan pernah commit API key, secret, atau private key wallet. Simpan di file `.env` (sudah di-ignore).
- Buat API key tanpa izin withdraw dan aktifkan whitelist IP.
- Urutan wajib: backtest → paper trading / testnet → live dengan modal kecil.
