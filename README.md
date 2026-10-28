# Docscan backend

Đổi tên dùng `PATCH /api/v1/documents/{id}` với JSON `{name, revision}`: kiểm tra chủ sở hữu và phiên bản, tăng revision, cập nhật tên/thời gian trong CSDL, giữ nguyên tệp. Không cần gửi lại multipart. CORS cho phép PATCH từ các origin đã cấu hình. Tên được kiểm tra cùng quy tắc với tạo tài liệu. Upload cloud dùng tối đa 4 luồng tệp và một HTTPX client chung cho mỗi batch; chỉ công bố revision khi tất cả tệp đã lưu, giữ cơ chế dọn dữ liệu dở dang và bảo vệ bản cũ.

FastAPI API cho web scan tài liệu và app mobile trong tương lai. Bản hiện tại đăng ký email/mật khẩu dùng ngay; xác minh email và quên mật khẩu chưa triển khai.

## Render Free và Supabase Free

Xem [hướng dẫn triển khai](docs/render-supabase.md). Backend hỗ trợ PostgreSQL và bucket Supabase riêng tư; bản local vẫn dùng SQLite. Docker build frontend từ commit đã ghim trong repo FE để web và API dùng cùng origin, giữ cookie HttpOnly hoạt động trên điện thoại. Không cần triển khai DLL Windows lên backend.

## Chạy trên máy này

Mở PowerShell tại `C:\Users\Khoa\duanthuctap\backendcamscanner`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Mở một terminal khác tại `C:\Users\Khoa\duanthuctap\camscanner2`:

```powershell
npm run dev -- --port 5173 --strictPort
```

Mở `http://localhost:5173`. FE proxy `/api` sang backend, kể cả khi dùng địa chỉ LAN của máy tính trên điện thoại. Không cần mở cổng backend ra mạng.

Máy hiện có CPython 3.12.10 tại `C:\Users\Khoa\AppData\Local\Programs\Python\Python312\python.exe`; `python` trên PATH trỏ sang MSYS2 nên không dùng nó để cài gói backend. Venv đang dùng `--system-site-packages` để tận dụng FastAPI/Uvicorn/Pillow có sẵn. Tái tạo môi trường độc lập trên máy khác:

```powershell
& 'C:\path\to\Python312\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Tài liệu API: `http://127.0.0.1:8000/docs`; hợp đồng JSON: `/openapi.json`. API có prefix `/api/v1` để có thể nâng phiên bản khi cần.

## Android/iPhone và trình duyệt trong ứng dụng

- Cùng Wi-Fi: mở URL Network mà Vite in ra, ví dụ `http://<IP-máy-tính>:5173`. Nhập ảnh, tài khoản và PDF dùng cùng origin qua proxy.
- Camera web và Web Share yêu cầu secure context. Để thử camera trên điện thoại, dùng HTTPS với chứng chỉ được thiết bị tin cậy hoặc tunnel HTTPS đã cấu hình tới FE. Không bỏ qua cảnh báo chứng chỉ. Thiết lập tunnel/hosting bên ngoài chưa nằm trong lần triển khai này.
- Với HTTPS, đặt `$env:DOCSCAN_SECURE_COOKIE='true'` và `$env:DOCSCAN_ALLOWED_ORIGINS='https://ten-mien-thu-nghiem'` trước khi khởi động backend. Vite cũng cần cho phép đúng hostname được sử dụng; không mở `allowedHosts: true` cho mọi host.
- Bấm PDF trong trang chỉnh ảnh sẽ tạo tệp rồi chờ API lưu thành công trước khi mở thẳng trang đọc PDF; không qua trang xem trước. Tài liệu mới và tài liệu mở từ kho dùng cùng trang đọc với Chỉnh sửa/Tải PDF. PDF dùng khung A4 dọc cố định, ảnh nằm vừa trang và giữ tỷ lệ; trình đọc trong web dùng cùng bố trí, không phụ thuộc plugin PDF của webview. Sau khi lưu có đường tải HTTPS với `Content-Disposition: attachment`. Chia sẻ/Lưu vào Tệp dùng file đã chuẩn bị nếu nền tảng hỗ trợ.
- Ảnh JPEG có MPF (được Pillow nhận diện là MPO) được lưu nguyên bản như JPEG; chỉ dùng ảnh chính, không lấy ảnh phụ làm trang mới. Lỗi kiểm tra ảnh chỉ rõ số trang và ảnh gốc/đã chỉnh; lỗi không thay đổi phiên bản đã lưu trước đó.
- Một số webview áp dụng chính sách tải/chia sẻ riêng. Nếu bị chặn, mở liên kết trong trình duyệt đầy đủ hoặc dùng Mở PDF/Chia sẻ. Không thể bảo đảm mọi webview và mọi phiên bản điện thoại bằng một API trình duyệt duy nhất.

## API dùng cho app

| Endpoint | Hành vi |
| --- | --- |
| `POST /api/v1/auth/register` | JSON `{email,password}`, mật khẩu 10–128 ký tự; header `X-Requested-With: docscan`. Tạo tài khoản, trả `{id,email}`. |
| `POST /api/v1/auth/session` | Web: JSON email/mật khẩu, header `X-Requested-With: docscan`, nhận cookie HttpOnly/SameSite. |
| `POST /api/v1/auth/token` | App: JSON email/mật khẩu, trả `{access_token,token_type,expires_in}`; dùng `Authorization: Bearer <token>` cho cùng API tài liệu. |
| `GET /api/v1/auth/me` | Thông tin tài khoản của phiên hiện tại. |
| `DELETE /api/v1/auth/session` | Thu hồi phiên cookie hoặc bearer hiện tại. |
| `GET /api/v1/documents?limit=20&offset=0` | Danh sách của chủ tài khoản; tổng số được tính độc lập với phân trang. |
| `POST /api/v1/documents` | Tạo tài liệu, multipart bên dưới. |
| `GET /api/v1/documents/{id}` | Metadata và URL riêng tư của ảnh/PDF trong phiên bản hiện tại. |
| `PUT /api/v1/documents/{id}` | Thay toàn bộ tập trang; phải gửi revision hiện tại. Xung đột trả 409. |
| `GET /api/v1/documents/{id}/pdf?revision=1&download=true` | Tải PDF có kiểm tra chủ sở hữu và phiên bản; không âm thầm tải phiên bản khác. |

Web dùng cookie nên các request thay đổi dữ liệu phải có `X-Requested-With: docscan` và origin hợp lệ. App dùng bearer không phụ thuộc cookie, domain web hay công nghệ FE; token cần được giữ trong kho bảo mật của app. Token mờ có hạn dùng 7 ngày và có thể thu hồi, không chứa thông tin tài khoản để client tự tin tưởng.

Multipart gồm đúng một trường `manifest` (JSON), một tệp `pdf`, và `original_0`, `rendered_0`, ... theo thứ tự trang. Không tự đặt `Content-Type` multipart khi dùng FormData; để client sinh boundary.

```json
{
  "schemaVersion": 1,
  "name": "Tài liệu",
  "revision": null,
  "pages": [{
    "id": "page-1",
    "original": {"width": 1200, "height": 1600, "mime": "image/jpeg"},
    "rendered": {"width": 900, "height": 1200, "mime": "image/jpeg"},
    "recipe": {
      "version": 1, "crop": {"kind": "full-frame"}, "rotation": 0,
      "dewarp": {"mode": "perspective-only"},
      "filter": {"mode": "original", "presetVersion": "original-v1", "parameters": {}}
    },
    "engineVersion": "your-renderer-version", "recipeKey": "your-recipe-key",
    "outcome": {"kind": "not-requested"}
  }]
}
```

Tạo mới gửi `revision: null`; cập nhật gửi số revision nhận từ server. API giữ nguyên ảnh nguồn và recipe để client sau này có thể sửa lại, không chỉ lưu PDF. Backend kiểm tra cấu trúc, định dạng, kích thước ảnh và số trang PDF; nội dung PDF phải được client tạo từ đúng ảnh rendered. Backend không chạy thuật toán chỉnh ảnh của SDK, không bảo đảm tính tương đương ngữ nghĩa giữa một PDF do client bên ngoài gửi với các ảnh đi kèm.

Khi tạo mới, client có thể gửi `Idempotency-Key` (16–80 ký tự chữ/số/gạch nối/gạch dưới). Gửi lại cùng key và cùng dữ liệu khi mất phản hồi trả về tài liệu đã tạo; dữ liệu khác hoặc tài liệu đã được sửa trả 409. Web tự giữ key cho cùng một lần chuẩn bị PDF để tránh tạo bản trùng khi thử lại.

## Lưu trữ, giới hạn và vận hành

- SQLite tại `data/docscan.sqlite3`; tệp tại `data/documents/<id>/<storage-id>/`, không public thư mục này. Sao lưu cả DB và thư mục tài liệu khi backend dừng để giữ một snapshot nhất quán.
- Mỗi tài liệu 1–50 trang; 25 MiB mỗi ảnh, 50 MB PDF, 200 MiB tổng request; tối đa 1.000 tài liệu và 2 GiB dữ liệu hiện hành mỗi tài khoản. Tổng dung lượng cloud còn chịu quota của Supabase Free. Ảnh nguồn tối đa 80 MP, ảnh rendered tối đa 16 MP. Có tối đa hai thao tác nhận/lưu upload đồng thời mỗi tiến trình.
- JPEG/PNG/WebP/AVIF được kiểm tra bằng Pillow. HEIC/HEIF gốc được giữ như nguồn riêng tư, kiểm tra chữ ký container và giới hạn metadata; backend không giải mã định dạng này. Khi mở sửa, SDK trên client kiểm tra và giải mã lại. Thumbnail chỉ sinh từ JPEG/PNG rendered đã kiểm tra.
- Ghi phiên bản mới vào thư mục riêng, cập nhật con trỏ DB theo revision trong giao dịch rồi mới dọn bản cũ. Lỗi trước commit giữ nguyên bản hiện hành; tải đang mở trên Windows có thể khiến bản cũ chưa xoá được. Crash có thể để lại thư mục upload dở.
- Khi backend đã dừng, chạy `.\.venv\Scripts\python.exe -m app.cleanup` để dọn các bản không được DB tham chiếu và đã cũ hơn một giờ. Lệnh giữ nguyên dữ liệu hiện hành, bỏ qua symlink và kiểm tra đường dẫn đích nằm trong thư mục tài liệu.
- Mật khẩu dùng scrypt với salt ngẫu nhiên; token chỉ lưu bản băm. Không đưa dữ liệu runtime hoặc bí mật vào Git. `.env.example` chỉ mô tả biến môi trường, không tự động được nạp.
- Khi triển khai: HTTPS, cookie Secure, origin cụ thể, reverse proxy có giới hạn kết nối/thời gian request và cấu hình trusted proxy, sao lưu/giám sát dung lượng. Cấu hình production yêu cầu PostgreSQL và Supabase Storage riêng tư; SQLite chỉ dùng local.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
```

Kiểm thử tự động dùng tài khoản giả, DB và tệp tạm; không gửi email hoặc gọi dịch vụ bên ngoài.
