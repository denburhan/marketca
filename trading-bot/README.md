# Grid Trading Bot (Spot)

Bot grid untuk exchange spot lewat [ccxt](https://github.com/ccxt/ccxt). Default memakai Bitget,
tapi exchange lain yang didukung ccxt (Binance, OKX, Bybit, dll.) bisa dipakai dengan mengganti `EXCHANGE` di `.env`.

## Cara kerja grid

Range harga `lower`–`upper` dibagi menjadi `grids` slot. Bot memasang limit **buy** di setiap level di bawah harga sekarang.
Begitu buy terisi, bot memasang limit **sell** satu level di atasnya. Begitu sell terisi, buy dipasang lagi. Begitu seterusnya.

- **Untung** saat harga naik-turun (sideways) di dalam range.
- **Rugi** saat harga turun terus keluar dari range: bot akan memegang koin yang nilainya turun.
  Karena itu ada `--stop-loss`.
- Jika harga naik terus di atas range, bot berhenti trading dan sisa USDT menganggur.

Bot dimulai hanya dengan USDT (tanpa market buy di awal).

## Instalasi

```bash
cd trading-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # isi API key hanya untuk mode live
```

## Urutan pemakaian (jangan dilompati)

### 1. Backtest dengan data historis

```bash
python main.py download --symbol SOL/USDT --timeframe 15m --days 90 --out data/sol.csv
python main.py backtest --csv data/sol.csv --lower 120 --upper 200 --grids 10 --investment 100 --stop-loss 110
```

Bandingkan hasilnya dengan **Buy & hold**. Coba beberapa range dan periode. Kalau grid kalah dari buy & hold
atau drawdown-nya tidak bisa kamu terima, jangan lanjut.

### 2. Paper trading (harga asli, order simulasi)

```bash
python main.py paper --lower 120 --upper 200 --grids 10 --investment 100 --stop-loss 110
```

Jalankan minimal 1–2 minggu. Tidak butuh API key.

### 3. Live (uang asli)

```bash
python main.py live --lower 120 --upper 200 --grids 10 --investment 20 --stop-loss 110 --i-understand-the-risk
```

- Mulai dengan modal kecil.
- State disimpan di `state.json`. Ctrl+C menghentikan bot, **order tetap terbuka di exchange**.
  Jalankan perintah yang sama untuk melanjutkan.
- Untuk berhenti total: hentikan bot, batalkan order di aplikasi exchange, lalu hapus `state.json`.
- Jangan ubah atau batalkan order bot secara manual saat bot berjalan.

## Parameter

| Parameter | Arti |
|---|---|
| `--symbol` | Pair, mis. `SOL/USDT` |
| `--lower`, `--upper` | Batas bawah dan atas range harga |
| `--grids` | Jumlah slot. Makin banyak, makin sering trading tapi profit per trade makin kecil |
| `--investment` | Modal dalam USDT, dibagi rata per grid |
| `--fee` | Fee per transaksi (default `0.001` = 0.1%) |
| `--stop-loss` | Jika harga ≤ nilai ini: batalkan semua order, jual semua koin, bot berhenti |

Bot menolak config yang profit per grid-nya habis dimakan fee.

## Keamanan

- Jangan pernah commit API key, secret, atau `.env`. Sudah di-ignore oleh `.gitignore`.
- Buat API key **tanpa izin withdraw** dan aktifkan **whitelist IP**.
- Jalankan bot live di VPS atau komputer yang menyala 24 jam.

## Keterbatasan yang perlu diketahui

- Backtest optimistis: menganggap limit order selalu terisi saat harga menyentuh level, tanpa slippage.
- Stop-loss dicek setiap `--interval` detik (default 15). Saat harga anjlok cepat, eksekusinya bisa lebih rendah.
- Jika bot crash tepat setelah memasang order tapi sebelum menyimpan state, order itu tidak tercatat.
  Cek order terbuka di exchange setelah crash.
- Kode live sudah diuji dengan exchange tiruan, **belum** dengan akun Bitget sungguhan.

## Tes

```bash
python -m pytest -q
```
