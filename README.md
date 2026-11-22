# Bio-Memory

**Trí nhớ cho AI của bạn, nằm trên máy của bạn.** · *A memory for your AI, kept on your own computer.* ([English below](#english))

Bio-Memory giữ trí nhớ cho AI bạn đang dùng. Hôm nay bạn kể, hôm sau AI vẫn nhớ. Điều đã cũ được đánh dấu là cũ, không bị trộn với điều mới.
Khi bạn bảo quên, nó xoá hẳn khỏi tệp dữ liệu và cho bạn xem bằng chứng. Trí nhớ nằm trong một tệp trên máy bạn. Bio-Memory không gửi nó đi đâu.

## Bạn dùng theo cách nào?

| Bạn là | Dùng | Lấy ở đâu |
|---|---|---|
| Người dùng bình thường | **Locaith Orb**, ứng dụng trợ lý có sẵn trí nhớ bên trong | <https://orb.locaith.ai> |
| Người dùng Claude Code, Claude Desktop hoặc Cursor | **Bio-Memory.exe** | [Tải bản mới nhất](https://github.com/locaith/bio-memory-ai-locaith/releases/latest) |

## Cài Bio-Memory.exe (Windows 10 hoặc 11, 64-bit)

1. Tải `Bio-Memory.exe` ở trang [Releases](https://github.com/locaith/bio-memory-ai-locaith/releases/latest).
2. Mở tệp vừa tải. Chương trình chưa có chữ ký số, nên Windows có thể báo "Windows protected your PC". Bấm **More info**, rồi **Run anyway**.
3. Trong cửa sổ Bio-Memory, bấm **Cài đặt**. Không cần quyền quản trị. Chương trình được đặt trong thư mục của riêng bạn và có biểu tượng trong Start.
4. Bấm **Nối với Claude Code**, **Nối với Claude Desktop** hoặc **Nối với Cursor**. Trước khi đổi gì, cửa sổ cho bạn xem nó sẽ đổi gì.
5. Mở lại ứng dụng AI đó và nói: *"Hãy nhớ giúp tôi: tôi thích cà phê đen, không đường."* Điều AI vừa nhớ hiện ngay trong cửa sổ Bio-Memory.

Nối Claude Code nghĩa là Bio-Memory thành trí nhớ của **mọi dự án** Claude Code trên máy. Ghi chú Claude Code đã có được chép vào trước. Sau đó trí nhớ riêng của Claude Code tắt đi, để mỗi phiên chỉ có một trí nhớ. Các tệp ghi chú cũ giữ nguyên, và bấm **Ngắt kết nối** là mọi thứ trở lại như trước.

**Gỡ:** mở Bio-Memory, chọn **Gỡ Bio-Memory khỏi máy**. Chương trình, biểu tượng và các kết nối được gỡ. Trí nhớ của bạn trong `%USERPROFILE%\Bio-Memory` vẫn giữ nguyên. Đó là của bạn.

## Riêng tư

- Bio-Memory chạy trên máy bạn và không gửi dữ liệu đi đâu.
- Có một điều cần biết: AI bạn nối (Claude, chẳng hạn) đọc những gì nó tìm thấy trong trí nhớ. Phần đó đi cùng cuộc trò chuyện tới công ty làm ra AI ấy, như mọi điều bạn gõ cho nó.
- Bio-Memory không giữ những gì trông như mật khẩu hay khoá bí mật.

## Chưa có

- Bản cho macOS và Linux.
- Chữ ký số của chương trình. Bản trên Microsoft Store đang chờ duyệt.

## Giấy phép

Bio-Memory.exe dùng miễn phí, cho cá nhân lẫn công việc, theo [giấy phép Bio-Memory](LICENSE.md). Từ bản 0.11, Bio-Memory phát hành dạng chương trình đã biên dịch; mã nguồn không công bố.
Các bản đến 0.8.2rc1 đã công bố mã nguồn theo giấy phép MIT. Chúng vẫn ở các tag `v0.6.1` và `v0.8.2rc1`.

Liên hệ: contact@locaith.com · Locaith Solution Tech, Việt Nam.

---

<a id="english"></a>

## English

Bio-Memory keeps the memory of the AI you use. Tell it something today and your AI still knows it tomorrow. What is out of date is marked as
out of date, not mixed in with what is current. When you say forget, it is erased from the data file, and you are shown the proof. The memory is
one file on your computer, and Bio-Memory sends it nowhere.

| You are | Use | Get it |
|---|---|---|
| Anyone | **Locaith Orb**, an assistant app with the memory built in | <https://orb.locaith.ai> |
| A Claude Code, Claude Desktop or Cursor user | **Bio-Memory.exe** | [Latest release](https://github.com/locaith/bio-memory-ai-locaith/releases/latest) |

**Install (Windows 10 or 11, 64-bit):**

1. Download `Bio-Memory.exe` from [Releases](https://github.com/locaith/bio-memory-ai-locaith/releases/latest).
2. Open it. The program is not signed yet, so Windows may say "Windows protected your PC". Choose **More info**, then **Run anyway**.
3. In the Bio-Memory window, choose **Install**. No administrator rights are needed: it goes into your own user folder, with an icon in Start.
4. Choose **Connect to Claude Code**, **Connect to Claude Desktop** or **Connect to Cursor**. The window shows you what it will change before
   it changes anything.
5. Open that AI app again and say: *"Please remember for me: I like black coffee, no sugar."* What it remembered shows in the Bio-Memory window.

Connecting Claude Code makes Bio-Memory the memory of **every** Claude Code project on the computer. Each project's existing notes are copied in
first. Then Claude Code's own memory is turned off, so that a session has one memory. The old note files stay as they are, and **Disconnect**
puts everything back as it was.

**Uninstall:** open Bio-Memory and choose **Remove Bio-Memory from this computer**. The program, its icons and its connections go. Your memory in
`%USERPROFILE%\Bio-Memory` stays where it is: it is yours.

**Privacy:** Bio-Memory runs on your computer and sends nothing anywhere. One thing to know: the AI you connect reads what it finds in the memory,
and that part travels with your conversation to the company that makes that AI, like anything you type to it. Bio-Memory does not keep what
looks like a password or a secret key.

**Not yet:** macOS and Linux builds; a signed program (a Microsoft Store edition is waiting for approval).

**License:** Bio-Memory.exe is free to use, personally and at work, under the [Bio-Memory License](LICENSE.md). From 0.11 on, Bio-Memory is
released as a compiled program and its source is not published. Releases up to 0.8.2rc1 were published as source under the MIT License, and
remain at the tags `v0.6.1` and `v0.8.2rc1`.

Contact: contact@locaith.com · Locaith Solution Tech, Vietnam.
