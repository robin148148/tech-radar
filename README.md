# Tech Radar — Hệ thống Săn Repo, FOSS, AI Skills & Thủ thuật Tự động

**Tech Radar** là Webapp không máy chủ (**Zero-Server**) vận hành hoàn toàn miễn phí trên hệ sinh thái GitHub:

1. **GitHub Actions (`.github/workflows/tech-radar.yml`)**: Tự động chạy lúc **06:00 sáng giờ Việt Nam** (`23:00 UTC`) hàng ngày để quét dữ liệu từ GitHub, Reddit, Hacker News, Dev.to và các nguồn RSS/Telegram tùy chỉnh.
2. **Gemini AI (`gemini-2.5-flash`)**: Đóng vai biên tập viên công nghệ — lọc bỏ tin rác/quảng cáo, chấm điểm 1–10 (chỉ giữ mục `>= 7`), phân loại vào 4 nhóm (`Repo/Code`, `Phần mềm miễn phí`, `AI Skills`, `Mẹo & Thủ thuật`), tóm tắt tiếng Việt súc tích và gợi ý ứng dụng thực tế.
3. **GitHub Pages (`index.html`)**: Giao diện Single-File siêu nhẹ, Mobile-First, hỗ trợ Dark/Light Mode, bộ lọc nhanh, tìm kiếm tức thì và lưu trữ Bookmark ngay trên trình duyệt.

---

## Cấu trúc dự án

```text
.
├── .github/workflows/tech-radar.yml   # Lịch chạy tự động 06:00 VN & auto-commit data/
├── config/sources.json                # Cấu hình nguồn quét (topics, subreddits, RSS, Telegram...)
├── data/
│   ├── latest.json                    # Tin mới lọc của lần chạy gần nhất
│   ├── archive.json                   # Kho báu đã lọc (tối đa 500 mục mới nhất)
│   └── seen_urls.json                 # Bộ nhớ chống trùng URL đã quét
├── scripts/
│   ├── fetchers.py                    # Module thu thập dữ liệu đa nguồn (try/except + timeout=15s)
│   ├── curator.py                     # Module chấm điểm & biên tập bằng Gemini API + fallback an toàn
│   └── main.py                        # Điều phối toàn bộ pipeline
├── index.html                         # Giao diện Webapp Single-File (Tailwind CDN + Vanilla JS)
├── requirements.txt                   # Thư viện Python
└── README.md                          # Tài liệu hướng dẫn
```

---

## Hướng dẫn triển khai lên GitHub (Từng bước)

### Bước 1: Tạo Repository và đẩy mã nguồn lên GitHub

1. Đăng nhập GitHub và tạo một repository mới (ví dụ: `tech-radar`, chọn **Public** để dùng GitHub Pages miễn phí).
2. Mở terminal tại thư mục dự án và chạy:

```bash
git init
git add .
git commit -m "feat: initialize Tech Radar zero-server webapp"
git branch -M main
git remote add origin https://github.com/<ten-github-cua-ban>/tech-radar.git
git push -u origin main
```

### Bước 2: Lấy và thêm `GEMINI_API_KEY` vào GitHub Secrets

1. Truy cập [Google AI Studio](https://aistudio.google.com/apikey) và tạo một **API Key** miễn phí cho Gemini.
2. Mở repository của bạn trên GitHub -> vào **Settings** -> **Secrets and variables** -> **Actions**.
3. Nhấn **New repository secret**:
   - **Name**: `GEMINI_API_KEY`
   - **Secret**: Dán API Key vừa tạo.
4. Nhấn **Add secret**.

> **Lưu ý:** `GITHUB_TOKEN` được GitHub Actions tự động cấp sẵn, bạn không cần tạo thủ công.

### Bước 3: Cấp quyền ghi cho GitHub Actions (nếu cần)

1. Vào **Settings** -> **Actions** -> **General**.
2. Kéo xuống mục **Workflow permissions**, chọn **Read and write permissions** -> nhấn **Save**.

### Bước 4: Chạy thử Workflow lần đầu

1. Chuyển sang tab **Actions** trên GitHub.
2. Chọn workflow **Tech Radar Daily Scan** ở cột bên trái.
3. Nhấn **Run workflow** -> **Run workflow** để chạy ngay lập tức (không cần đợi tới 06:00 sáng hôm sau).
4. Khi workflow báo xanh, thư mục `data/` sẽ tự động được cập nhật và commit lên nhánh `main`.

### Bước 5: Bật GitHub Pages

1. Vào **Settings** -> **Pages**.
2. Ở mục **Build and deployment**:
   - **Source**: chọn `Deploy from a branch`.
   - **Branch**: chọn `main`, thư mục `/ (root)` -> nhấn **Save**.
3. Đợi 1–2 phút, truy cập địa chỉ `https://<ten-github-cua-ban>.github.io/tech-radar/` trên máy tính hoặc điện thoại (có thể chọn *Thêm vào màn hình chính* để dùng như app).

---

## Tùy chỉnh nguồn tin (`config/sources.json`)

Bạn chỉ cần sửa trực tiếp file `config/sources.json` trên GitHub mà không cần đụng vào mã Python:

- **`github.topics`**: Thêm/bớt chủ đề GitHub muốn săn (mặc định: `agent-skills`, `mcp-server`, `self-hosted`, `automation`, `free-software`, `cli-tool`, `ai-tools`).
- **`reddit.subreddits`**: Danh sách subreddit (tự động hỗ trợ fallback sang RSS nếu Reddit JSON bị giới hạn).
- **`hacker_news`**: Chỉnh `min_points` (mặc định `50`) và `hours_back` (mặc định `48`).
- **`devto.tags`**: Danh sách tag trên Dev.to.
- **`custom_rss`**: Thêm blog công nghệ (RSS/Atom) hoặc **kênh Telegram công khai** dạng `https://t.me/s/<ten_kenh>` bằng cách thêm phần tử vào mảng `custom_rss` và đặt `"enabled": true`.

---

## Chạy thử cục bộ (Local Testing)

```bash
# 1. Cài thư viện
pip install -r requirements.txt

# 2. (Tùy chọn) Đặt khóa Gemini API để chấm điểm AI thật
# PowerShell:
$env:GEMINI_API_KEY="your-api-key"
# Nếu không đặt GEMINI_API_KEY, hệ thống tự động chạy chế độ fallback an toàn (is_curated: false).

# 3. Chạy pipeline thu thập & sinh dữ liệu vào thư mục data/
python scripts/main.py

# 4. Mở giao diện web cục bộ
python -m http.server 8000
# Sau đó mở trình duyệt tại http://localhost:8000
```

Các cờ hữu ích khi kiểm thử `scripts/main.py`:
- `--dry-run`: Chạy thử và in thống kê mà không ghi đè thư mục `data/`.
- `--no-ai`: Bỏ qua Gemini API, ép chạy chế độ lọc thô dự phòng.
- `--skip-seen`: Bỏ qua danh sách `data/seen_urls.json` để quét lại toàn bộ.
- `--max-candidates 20`: Giới hạn số ứng viên gửi AI.
