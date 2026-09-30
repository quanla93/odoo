# Hướng dẫn sử dụng BHSoft SendGrid Mailer 1.2

Tài liệu này áp dụng cho module kỹ thuật `bhs_sendgrid_mailer`, phiên bản `20.0.1.2.0`, chạy trên Odoo 20.

## 1. Vai trò người dùng

### Quản trị viên hệ thống

Quản trị viên có thể:

- cấu hình SendGrid và Google Service Account;
- cấu hình webhook, API polling, lịch gửi và thời gian lưu dữ liệu;
- tạo, kích hoạt hoặc vô hiệu hóa suppression thủ công;
- tạo và quản lý nguồn Google Sheets;
- xem queue, lịch sử provider event và kết quả đồng bộ.

### Người dùng nội bộ

Người dùng nội bộ có thể:

- nhập dữ liệu từ Excel;
- xem và xử lý hàng đợi email;
- xem suppression và lịch sử event ở chế độ chỉ đọc;
- xem kết quả các lần đồng bộ Google Sheets.

Thông tin bí mật như API key, webhook verification key và Google Service Account JSON chỉ dành cho quản trị viên hệ thống.

## 2. Cấu hình lần đầu

Mở **BHSoft Mailer → Settings**.

### 2.1 SendGrid

1. Nhập **Sender Email**. Địa chỉ này phải được SendGrid cho phép gửi.
2. Dán API key mới vào **SendGrid API Key**.
3. Bấm **Test Connection**.
4. Bấm **Save**.

Sau khi lưu, hệ thống không hiển thị lại API key. Để giữ khóa hiện tại, để trống ô nhập trong lần cấu hình tiếp theo. Chỉ bấm **Clear API Key** khi thực sự muốn xóa khóa đã lưu.

Nếu không cấu hình SendGrid API key, module chuyển sang hàng đợi email tiêu chuẩn của Odoo. Khi đó phải cấu hình Outgoing Mail Server của Odoo riêng.

### 2.2 Theo dõi trạng thái SendGrid

Trong **Update Mode**, chọn một trong các chế độ:

- **Disable Tracking**: không nhận cập nhật trạng thái;
- **Webhook**: nhận event theo thời gian gần thực từ SendGrid;
- **API Polling**: Odoo định kỳ truy vấn SendGrid Activity API;
- **Webhook + API Polling**: dùng cả hai cơ chế.

Webhook là lựa chọn ưu tiên nếu Odoo có domain HTTPS công khai.

#### Cấu hình webhook

1. Sao chép **Webhook URL** trong Settings.
2. Trong SendGrid, mở phần cấu hình Event Webhook.
3. Dán URL và bật chữ ký webhook.
4. Bật tối thiểu các event cần theo dõi:
   - Processed;
   - Delivered;
   - Open;
   - Click;
   - Bounced;
   - Dropped;
   - Spam Reports;
   - Unsubscribe;
   - Group Unsubscribe nếu dùng SendGrid ASM.
5. Sao chép verification public key từ SendGrid và dán vào **Webhook Verification Key** trong Odoo.
6. Chọn **Webhook** hoặc **Webhook + API Polling**, rồi lưu Settings.
7. Dùng chức năng gửi test có chữ ký từ SendGrid. Odoo không chấp nhận test webhook không có chữ ký hợp lệ.

Webhook phải dùng HTTPS khi triển khai qua Internet. Event không có chữ ký, chữ ký sai hoặc timestamp quá cũ sẽ bị từ chối.

#### Cấu hình API polling

1. Chọn **API Polling** hoặc **Webhook + API Polling**.
2. Bật **Enable API Polling**.
3. Chọn số phút giữa các lần đồng bộ.
4. Chọn **Lookback Period** phù hợp.
5. Lưu Settings.

API polling cần API key có quyền đọc Activity API tương ứng trong gói SendGrid đang sử dụng.

### 2.3 Lịch gửi

Trong nhóm **Scheduled Jobs and Queue**:

- bật **Enable Scheduled Sending** để Odoo tự xử lý queue;
- **Sending Interval** là số phút giữa các lần chạy;
- **Batch Size** là số email tối đa được nhận xử lý trong mỗi lần;
- **Minimum Delay** và **Maximum Delay** tạo khoảng chờ ngẫu nhiên tích lũy giữa các email;
- **Odoo Status Sync Interval** cập nhật trạng thái cho nhánh gửi qua hàng đợi Odoo.

Nên bắt đầu bằng batch nhỏ, kiểm tra kết quả rồi mới tăng số lượng.

### 2.4 Chống trùng trong lần nhập

Trong **Deduplication Mode**:

- **Deduplicate emails within each import**: trong cùng một batch Excel, chỉ dòng đầu tiên của một email chuẩn hóa được giữ Pending; các dòng sau được đánh dấu Duplicate;
- **Do not deduplicate**: không lọc trùng theo batch.

Đây là chống trùng trong một lần nhập, không phải danh sách toàn cục các email đã gửi. Một email đã Delivered vẫn có thể được gửi ở chiến dịch khác nếu không bị suppression.

## 3. Nhập người nhận từ Excel

Mở **BHSoft Mailer → Import Excel File**.

### 3.1 Chuẩn bị file

File phải có một dòng tiêu đề. Các cột được nhận diện:

| Cột | Bắt buộc | Nội dung |
|---|---|---|
| Contact | Không | Tên người nhận |
| Email | Có | Địa chỉ nhận email |
| Subject | Có | Tiêu đề email |
| Body | Có | Nội dung email |
| HubSpot ID | Không | Mã tham chiếu nghiệp vụ |
| LinkedIn | Không | URL LinkedIn |
| Company | Không | Tên công ty |
| Company Website | Không | Website công ty |

Các cột `No.`, `Sending Status`, `Sent date` và `Sent time` có thể tồn tại nhưng sẽ bị bỏ qua.

### 3.2 Thực hiện nhập

1. Chọn file Excel.
2. Bấm **Import Data**.
3. Đọc thông báo tổng kết.
4. Mở **BHSoft Mailer → Queue** để kiểm tra các dòng Pending.
5. Mở **All Messages** nếu cần xem cả Duplicate, Skipped hoặc trạng thái đã hoàn tất.

Nếu email nằm trong suppression list, hệ thống vẫn tạo một dòng để người vận hành có thể kiểm tra, nhưng trạng thái là **Skipped**, có liên kết suppression và lý do chặn. Dòng này không được gửi.

## 4. Đồng bộ từ Google Sheets

Google Sheets yêu cầu Google Service Account có quyền Editor vì Odoo phải ghi một Row ID ổn định trở lại Sheet.

### 4.1 Cấu hình Google credentials

1. Tạo Google Cloud Service Account.
2. Bật Google Sheets API cho project.
3. Tạo JSON key.
4. Trong **BHSoft Mailer → Settings**, dán toàn bộ JSON vào **Service Account JSON**.
5. Bấm **Validate Credentials**.
6. Bấm **Save**.
7. Chia sẻ spreadsheet cho email Service Account hiển thị trong Settings với quyền **Editor**.

Không đưa JSON key vào Git, chatter, tài liệu chia sẻ hoặc log.

### 4.2 Chuẩn bị Sheet

Sheet nên có các cột tương tự Excel và một cột dành cho `Odoo Row ID`. Có thể để cột này chưa tồn tại hoặc để trống ở các dòng mới; Odoo sẽ tạo header ở cột trống đầu tiên trong range và ghi UUID cho từng dòng.

Không sao chép `Odoo Row ID` từ dòng này sang dòng khác. Hai dòng dùng cùng Row ID sẽ bị cách ly để tránh ghi đè hoặc gửi nhầm.

### 4.3 Tạo nguồn Sheet

1. Mở **BHSoft Mailer → Google Sheet Sources**.
2. Tạo nguồn mới.
3. Nhập:
   - tên nguồn;
   - Spreadsheet ID lấy từ URL;
   - tên tab chính xác;
   - range cột, ví dụ `A:M`;
   - số dòng header;
   - mapping cột nếu tiêu đề khác mặc định.
4. Bấm **Test Connection**.
5. Bấm **Sync Now**.
6. Kiểm tra nút thống kê **Messages** và **Sync Runs**.
7. Chỉ bật **Auto Sync** sau khi đồng bộ thủ công thành công.

### 4.4 Quy tắc cập nhật

- Dòng mới tạo queue Pending.
- Dòng không thay đổi không tạo queue mới.
- Dòng thay đổi khi queue còn Pending sẽ cập nhật queue hiện tại.
- Dòng thay đổi sau khi đã xử lý, lên lịch hoặc hoàn tất được đánh dấu source conflict thay vì ghi đè.
- Dòng bị xóa khỏi Sheet được đánh dấu Missing From Source, không tự động hủy email đã lên lịch.
- Dòng có địa chỉ bị suppression được tạo hoặc cập nhật thành Skipped.
- Nếu suppression được quản trị viên vô hiệu hóa, lần sync tiếp theo có thể mở lại đúng dòng đã Skipped vì suppression đó.
- Nếu sửa email bị suppression thành một email hợp lệ không bị chặn, lần sync tiếp theo có thể đưa dòng trở lại Pending.

Mở **Sync Runs** từ form nguồn để xem các bộ đếm Created, Updated, Duplicate, Skipped, Conflict và Error. Mở từng run để xem kết quả theo từng dòng.

Hướng dẫn Google Sheets chi tiết hơn nằm trong `docs/GOOGLE_SHEETS_SYNC.md`.

## 5. Kiểm tra và gửi queue

### 5.1 Kiểm tra trước khi gửi

Mở **BHSoft Mailer → Queue** và kiểm tra:

- tên và email người nhận;
- subject và body;
- trạng thái nguồn Sheet nếu có;
- thời điểm dự kiến gửi;
- suppression relation;
- các dòng source conflict hoặc source missing.

### 5.2 Gửi thủ công

1. Chọn các dòng Pending.
2. Bấm **Send Now**.
3. Hệ thống chỉ xử lý tối đa Batch Size đã cấu hình.
4. Các email trong batch được lên lịch với khoảng delay ngẫu nhiên tích lũy.
5. Suppression được kiểm tra lại khi claim queue và ngay trước khi gọi provider.

Dòng bị suppression ở kiểm tra cuối sẽ chuyển sang Skipped và không làm dịch chuyển lịch của các email hợp lệ còn lại.

### 5.3 Gửi tự động

Khi **Enable Scheduled Sending** được bật, cron định kỳ lấy các dòng Pending theo batch size và thực hiện cùng quy tắc như Send Now.

### 5.4 Hủy email đã lên lịch

1. Chọn dòng có trạng thái Pending hoặc Queued phù hợp.
2. Bấm **Cancel Scheduled**.
3. Xem trạng thái và phần **Cancellation** trên form queue.

Đối với SendGrid, việc hủy phụ thuộc vào batch ID và thời điểm provider còn cho phép hủy. Nếu hủy thất bại, hệ thống ghi lỗi để người vận hành xử lý thay vì báo thành công giả.

Khi một suppression mới được tạo, cron enforcement cũng cố gắng hủy các email tương lai cho địa chỉ đó. Email đã được provider gửi đi không thể thu hồi.

## 6. Hiểu các trạng thái queue

| Trạng thái | Ý nghĩa | Cách xử lý |
|---|---|---|
| Pending | Chờ xử lý | Kiểm tra rồi gửi hoặc chờ cron |
| Processing | Đang được worker xử lý | Không gửi lại thủ công |
| Queued | Đã submit hoặc lên lịch tại provider | Theo dõi trạng thái hoặc hủy nếu còn kịp |
| Sent | Đã tới thời điểm gửi/được Odoo ghi nhận đã gửi | Chờ event chi tiết nếu có |
| Delivered | Provider báo giao thành công | Không cần xử lý |
| Opened / Clicked | Người nhận đã tương tác | Không cần xử lý |
| Failed | Lỗi kỹ thuật có thể xem xét retry | Đọc Error Details trước khi Retry |
| Provider Outcome Unknown | Không biết provider đã nhận hay chưa | Không retry ngay; đối soát SendGrid trước |
| Bounced | Hard bounce; email được suppression | Không retry nếu chưa điều tra và deactivate suppression |
| Dropped | Provider từ chối/drop | Kiểm tra lý do; không tự động suppression |
| Spam Report | Người nhận báo spam; email được suppression | Không gửi lại |
| Unsubscribed | Người nhận hủy đăng ký; email được suppression | Không gửi lại |
| Duplicate | Trùng theo quy tắc import/source | Không gửi |
| Skipped | Bị bỏ qua, thường do suppression | Xem Error Details và suppression relation |
| Cancelled | Đã hủy trước khi gửi | Không retry tự động |

Chỉ dùng **Retry** cho dòng Failed sau khi đã sửa nguyên nhân kỹ thuật. Không retry dòng Unknown trước khi đối soát vì provider có thể đã nhận email dù Odoo không nhận được phản hồi HTTP.

## 7. Quản lý Suppression List

Mở **BHSoft Mailer → Suppression List**.

### 7.1 Khi nào hệ thống tự suppression

Hệ thống tự động chặn khi nhận event hợp lệ:

- Hard Bounce;
- Spam Report;
- Unsubscribe;
- Group Unsubscribe.

Hệ thống không tự động chặn vì:

- Deferred;
- Dropped;
- Delivered;
- Open;
- Click.

### 7.2 Tạo chặn thủ công

Quản trị viên:

1. Bấm **New**.
2. Nhập email.
3. Chọn **Manual Block**.
4. Giữ **Active** bật.
5. Lưu.

Email được chuẩn hóa bằng cách bỏ khoảng trắng đầu/cuối và chuyển thành chữ thường. Không thể tạo hai suppression cho cùng một email chuẩn hóa.

### 7.3 Vô hiệu hóa suppression

1. Mở suppression.
2. Tắt **Active**.
3. Lưu.

Không xóa suppression đã được queue tham chiếu. Vô hiệu hóa giúp giữ lại dấu vết lịch sử và ngăn liên kết cũ bị mất.

Event provider có timestamp cũ hơn thời điểm vô hiệu hóa không được phép tự kích hoạt lại suppression. Một definitive event thực sự mới hơn vẫn có thể kích hoạt lại vì đó là tín hiệu chặn mới từ người nhận/provider.

### 7.4 Sửa email suppression

Suppression đã được queue tham chiếu không thể đổi email. Nếu cần:

1. Vô hiệu hóa bản ghi cũ.
2. Tạo suppression mới với email đúng.

## 8. Xem lịch sử và xử lý sự cố

### Queue và All Messages

- **Queue** chỉ hiển thị các dòng đang chờ/đang xử lý/đã lên lịch.
- **All Messages** hiển thị tất cả trạng thái.
- Dùng bộ lọc **Failed / Review**, **Duplicate / Skipped**, **Cancelled**, **Source Conflicts** hoặc **Missing From Source**.
- Mở form để xem **Error Details**, **SendGrid Events** và **API Sync History**.

### Các lỗi thường gặp

#### Excel không nhập được

- Kiểm tra có dòng header.
- Kiểm tra đủ Email, Subject và Body.
- Kiểm tra định dạng email.
- Kiểm tra file có phải định dạng Excel được hỗ trợ.

#### Google Sheets báo 403

- Chia sẻ Sheet cho đúng Service Account email.
- Cấp quyền Editor.

#### Google Sheets báo 404 hoặc không tìm thấy tab

- Kiểm tra Spreadsheet ID.
- Kiểm tra tên tab, bao gồm chữ hoa, chữ thường và khoảng trắng.

#### Google Sheets không ghi được Row ID

- Kiểm tra quyền Editor.
- Kiểm tra cột Row ID không bị protected.
- Kiểm tra configured range còn một cột trống để tạo `Odoo Row ID`.

#### Webhook không cập nhật

- Kiểm tra Odoo có URL HTTPS công khai.
- Kiểm tra verification public key.
- Kiểm tra Update Mode có Webhook.
- Kiểm tra SendGrid đã bật đúng event.
- Xem **Last received** trong Settings.
- Không gửi payload thủ công không có chữ ký; endpoint bắt buộc xác thực chữ ký.

#### Dòng có trạng thái Unknown

- Tra cứu SendGrid Activity bằng message ID.
- Nếu provider đã nhận, không retry.
- Nếu xác nhận chắc chắn provider chưa nhận, xử lý theo quy trình nội bộ trước khi retry hoặc tạo queue mới.

## 9. Chính sách lưu dữ liệu

Mở **BHSoft Mailer → Settings → Data Retention**.

Mặc định:

- queue terminal không đến từ Google Sheets: 90 ngày;
- provider events và diagnostic logs: 30 ngày;
- Google Sheet row audit: 30 ngày;
- Google Sheet run summaries: 365 ngày;
- suppression: giữ vô thời hạn.

Nhập `0` để tắt riêng loại cleanup đó. Không được nhập số âm.

Queue terminal của Google Sheets được compact thay vì xóa. Hệ thống bỏ dữ liệu nội dung không còn cần thiết nhưng giữ Sheet source, Row ID, hash và trạng thái cuối để lần sync sau không tái tạo và gửi lại dòng cũ.

Cleanup chạy qua autovacuum của Odoo theo batch. Thay đổi retention không nhất thiết xóa dữ liệu ngay lập tức.

## 10. Checklist vận hành an toàn

Trước một chiến dịch lớn:

1. Test SendGrid connection.
2. Xác nhận Sender Email đã được xác thực.
3. Xác nhận webhook verification key còn hiệu lực.
4. Kiểm tra Last received của webhook hoặc API polling job.
5. Import hoặc sync một batch nhỏ.
6. Kiểm tra Duplicate, Skipped, source conflict và suppression.
7. Gửi thử tới danh sách nội bộ được phép.
8. Xác nhận Delivered/event cập nhật đúng.
9. Mới bật lịch gửi hoặc tăng Batch Size.

Không bao giờ đưa API key, webhook key, Google Service Account JSON, danh sách khách hàng thật, database dump hoặc filestore vào Git hay gói ZIP addon.
