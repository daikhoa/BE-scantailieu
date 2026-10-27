# Triển khai thử trên Render Free và Supabase Free

Ba repo vẫn độc lập. Render chạy một web service Docker từ repo BE, build FE từ commit cố định trong `Dockerfile`, phục vụ web và `/api/v1` cùng origin. Ảnh vẫn xử lý trên thiết bị bằng SDK/WASM; backend lưu tài khoản, phiên, metadata và tệp riêng tư. Luồng email/mật khẩu hiện tại được giữ nguyên, chưa dùng Supabase Auth.

## Supabase

Tạo tổ chức Free và project test. Tắt tự động cấp quyền cho bảng mới và bật tự động RLS. Backend tạo schema `docscan` riêng, bật RLS trên các bảng; schema này không được đưa vào Data API. Không thêm policy công khai cho bảng hoặc bucket.

Trong **Connect → Direct → Session pooler**, lấy URI dùng IPv4, port 5432. Có thể bỏ phần `:[YOUR-PASSWORD]` khỏi URI và nhập mật khẩu ở biến `DATABASE_PASSWORD` riêng; driver nhận mật khẩu trực tiếp nên không cần tự URL-encode. Không dùng IPv4 add-on trả phí.

Lấy project URL và **server secret key** trong Settings → API Keys. Không dùng publishable/anon key cho backend, không đưa server secret vào FE, Git hoặc chat.

## Render

Đăng nhập bằng GitHub của chủ repo. Tạo Blueprint từ repo BE hoặc Web Service với các giá trị tương đương:

| Mục | Giá trị |
| --- | --- |
| Repository | `daikhoa/BE-scantailieu` |
| Branch | `main` |
| Runtime | Docker |
| Instance type | Free |
| Region | Singapore |
| Health check | `/api/v1/health` |
| Auto deploy | On commit |

`render.yaml` chỉ tạo web service Free, không tạo Render Postgres, disk hoặc dịch vụ trả phí. Docker chạy bằng user không có quyền root. Cổng lấy từ `PORT` của Render; tắt access log để tránh ghi đường dẫn tài liệu riêng tư. Chỉ tin forwarded headers khi chạy sau managed ingress của Render.

Biến môi trường:

| Biến | Giá trị |
| --- | --- |
| `DOCSCAN_ENV` | `production` |
| `DOCSCAN_SECURE_COOKIE` | `true` |
| `DATABASE_URL` | URI Session pooler của project |
| `DATABASE_PASSWORD` | Mật khẩu database nếu chưa nằm trong URI |
| `SUPABASE_URL` | Project URL `https://<project-ref>.supabase.co` |
| `SUPABASE_SECRET_KEY` | Server secret key, chỉ đặt trên Render |
| `SUPABASE_STORAGE_BUCKET` | `docscan-private` |

Backend tự tạo bucket riêng tư nếu chưa có; từ chối khởi động nếu bucket đang public, hoặc production thiếu PostgreSQL, Storage hay cookie Secure. File ở thư mục tạm chỉ phục vụ kiểm tra/upload, được dọn sau khi lưu. Metadata/công thức chỉnh sửa nằm trong PostgreSQL; ảnh nguồn, ảnh kết quả, thumbnail, manifest và PDF nằm trong Supabase Storage. Mỗi tệp PDF tối đa **50 MB** theo giới hạn Free.

API kiểm tra phiên và chủ sở hữu trước khi cấp URL tải có hạn 60 giây. Không có URL public vĩnh viễn. Tệp cũ chỉ bị dọn sau khi con trỏ phiên bản mới đã commit; upload dang dở được ghi vào hàng chờ dọn trong DB. Khi cần, chạy `python -m app.cleanup` với cấu hình cloud để dọn tối đa 50 phiên bản không còn được tham chiếu, cũ hơn một giờ. Lệnh kiểm tra bản hiện hành trước khi xóa. Nếu Render Free không có shell, có thể chạy từ máy local với cùng cấu hình cloud qua biến môi trường riêng.

## Kiểm tra sau deploy

Mở URL Render, đăng ký/đăng nhập, nhập ảnh, xem PDF, lưu rồi mở sửa. Thử thêm/xóa trang, lưu cùng tài liệu và tải PDF. Đăng xuất và thử tài khoản khác để xác nhận không truy cập được tài liệu của tài khoản đầu. Redeploy rồi kiểm tra tài liệu còn nguyên. Database cloud bắt đầu độc lập; tài khoản/tài liệu SQLite local không tự chuyển sang Supabase.

CI chạy bộ API trên SQLite và PostgreSQL 17 tạm, đồng thời build/chạy thử container. Chế độ test PostgreSQL chỉ cho phép database `docscan_ci` ở localhost và dọn riêng schema của DB test này; không trỏ vào Supabase thật.

## Cập nhật

Thay đổi BE: commit/push `main`, Render tự deploy. Thay đổi FE: push repo FE, cập nhật `DOCSCAN_FRONTEND_REF` trong Dockerfile BE sang SHA đầy đủ của commit FE đã kiểm tra rồi push BE. Cách này ghim chính xác nguồn của mỗi bản deploy. Không đổi URL clone sang repo riêng tư nếu chưa chuẩn bị cách cấp quyền build; hiện build đọc repo FE public mà không cần token GitHub trong image. Thư viện đã được FE đóng gói trong `vendor/`, không chạy thành service riêng.

## Giới hạn gói Free

Render nghỉ sau 15 phút không có truy cập và có thể mất khoảng một phút để thức lại. Supabase Free hiện có 500 MB database, 1 GB file storage, giới hạn băng thông và có thể pause sau một tuần không hoạt động. Đây là môi trường test, không phải cam kết chạy liên tục. Xem [Render Free](https://render.com/docs/free), [Supabase pricing](https://supabase.com/pricing) và [giới hạn file](https://supabase.com/docs/guides/storage/uploads/file-limits).
