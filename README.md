# Bio-Memory

**Trí nhớ cho AI của bạn, nằm trên máy của bạn.** · *A memory for your AI, kept on your own computer.* ([English below](#english))

Bio-Memory giữ trí nhớ cho AI bạn đang dùng. Hôm nay bạn kể, hôm sau AI vẫn nhớ. Điều đã cũ được đánh dấu là cũ, không bị trộn với điều mới.
Khi bạn bấm Quên trong cửa sổ Bio-Memory, điều đó bị xoá hẳn khỏi tệp dữ liệu và bạn được xem kết quả kiểm tra. Khi bạn bảo AI quên, điều đó chỉ được ẩn đi; muốn xoá hẳn, bạn bấm Quên trong cửa sổ.
Trí nhớ nằm trên máy bạn. Bio-Memory không tự gửi nó đi đâu.

## Bạn dùng theo cách nào?

| Bạn là | Dùng | Lấy ở đâu |
|---|---|---|
| Người dùng bình thường | **Locaith Orb**, ứng dụng trợ lý có sẵn trí nhớ bên trong | <https://orb.locaith.ai> |
| Người dùng Claude Code, Claude Desktop, Codex, Gemini CLI, Cursor, Windsurf, Antigravity, VS Code hoặc ứng dụng AI khác | **Bio-Memory** (Windows, macOS, Linux) | Một dòng lệnh bên dưới, hoặc [tải bản mới nhất](https://github.com/locaith/bio-memory-ai-locaith/releases/latest) |

## Cài bằng một dòng lệnh

| Máy | Dán dòng này vào |
|---|---|
| Windows, PowerShell | `irm https://github.com/locaith/bio-memory-ai-locaith/releases/latest/download/install.ps1 \| iex` |
| Windows, cmd | `curl -fsSL https://github.com/locaith/bio-memory-ai-locaith/releases/latest/download/install.cmd -o "%TEMP%\bio-memory-install.cmd" && "%TEMP%\bio-memory-install.cmd"` |
| macOS, Linux | `curl -fsSL https://github.com/locaith/bio-memory-ai-locaith/releases/latest/download/install.sh \| sh` |

Dòng lệnh tải chương trình từ bản phát hành mới nhất trên GitHub và cho bạn thấy tiến trình tải. Nó kiểm tra tệp tải về khớp mã SHA-256 của bản phát hành, rồi cài vào thư mục của riêng bạn. Không cần quyền quản trị, không cần sudo. Sau đó bạn có lệnh `bio-memory`, và lệnh này dẫn bạn qua các bước đầu:

1. chọn Tiếng Việt hoặc English;
2. chọn chỗ cất trí nhớ (mặc định là thư mục `Bio-Memory` trong thư mục người dùng);
3. nối với các ứng dụng AI có trên máy: Claude Code, Claude Desktop, Codex, Gemini CLI, Cursor, Windsurf, Antigravity, VS Code. Trước khi ghi, lệnh cho bạn xem sẽ đổi gì, và tệp nào cũng được sao lưu trước;
4. tuỳ chọn: đọc tài liệu trong các thư mục bạn chọn để lập hồ sơ doanh nghiệp (đơn vị của bạn, con người, hợp đồng và việc tới hạn). Việc đọc làm ngay trên máy; hồ sơ hiện trong thẻ **Doanh nghiệp** của cửa sổ;
5. mở cửa sổ Bio-Memory.

Các lệnh dùng sau:

| Lệnh | Làm gì |
|---|---|
| `bio-memory` | lần đầu: các bước trên; sau đó: trí nhớ ở đâu, ứng dụng nào đã nối |
| `bio-memory open` | mở cửa sổ: xem AI đã nhớ gì, xoá hẳn một điều |
| `bio-memory status` | bản đang chạy, trí nhớ, các ứng dụng AI đã nối |
| `bio-memory connect` | xem và nối ứng dụng AI; `bio-memory connect --print <tên>` in đoạn cấu hình để dán vào ứng dụng khác, như Trae |
| `bio-memory doctor` | kiểm máy và chỉ cách sửa |
| `bio-memory update` | cài bản mới (cửa sổ cũng tự cập nhật mỗi lần mở) |
| `bio-memory uninstall` | gỡ chương trình; trí nhớ giữ nguyên |
| `bio-memory profile` | hồ sơ doanh nghiệp đọc từ tài liệu của bạn |
| `bio-memory help` | mọi lệnh |

`BIO_MEMORY_LANG=vi` hoặc `en` chọn ngôn ngữ mà không hỏi. `NO_COLOR=1` tắt màu.

## Hoặc tải Bio-Memory.exe (Windows 10 hoặc 11, 64-bit)

1. Tải `Bio-Memory.exe` ở trang [Releases](https://github.com/locaith/bio-memory-ai-locaith/releases/latest).
2. Mở tệp vừa tải. Chương trình chưa có chữ ký số, nên Windows có thể báo "Windows protected your PC". Bấm **More info**, rồi **Run anyway**.
3. Trong cửa sổ Bio-Memory, bấm **Cài đặt**. Không cần quyền quản trị. Chương trình được đặt trong thư mục của riêng bạn và có biểu tượng trong Start.
4. Bấm **Nối với Claude Code**, **Nối với Claude Desktop** hoặc **Nối với Cursor**. Trước khi đổi gì, cửa sổ cho bạn xem nó sẽ đổi gì.
5. Mở lại ứng dụng AI đó và nói: *"Hãy nhớ giúp tôi: tôi thích cà phê đen, không đường."* Điều AI vừa nhớ hiện ngay trong cửa sổ Bio-Memory.

Nối Claude Code nghĩa là Bio-Memory thành trí nhớ của **mọi dự án** Claude Code trên máy. Ghi chú Claude Code đã có được chép vào trước. Sau đó trí nhớ riêng của Claude Code tắt đi, để mỗi phiên chỉ có một trí nhớ. Các tệp ghi chú cũ giữ nguyên, và bấm **Ngắt kết nối** là mọi thứ trở lại như trước.

**Gỡ:** gõ `bio-memory uninstall`, hoặc mở Bio-Memory và chọn **Gỡ Bio-Memory khỏi máy**. Chương trình, biểu tượng, lệnh `bio-memory` và các kết nối được gỡ. Trí nhớ của bạn trong thư mục `Bio-Memory` (trên Windows là `%USERPROFILE%\Bio-Memory`) vẫn giữ nguyên. Đó là của bạn.

## Riêng tư

- Bio-Memory chạy trên máy bạn và không tự gửi dữ liệu đi đâu. Dữ liệu chỉ rời máy ở nơi bạn nối nó với thứ bên ngoài.
- AI bạn nối đọc những gì nó tìm thấy trong trí nhớ, và phần đó đi cùng cuộc trò chuyện tới công ty làm ra AI ấy. Nếu bạn bật cổng web, hoặc nối một model chạy trên mạng trong cửa sổ nâng cao, trí nhớ được gửi tới đúng nơi bạn đã chọn.
- Quên trong Bio-Memory không xoá lịch sử trò chuyện mà ứng dụng AI tự giữ. Nếu bạn mở lại một phiên cũ, AI vẫn có thể thấy điều đã nói trong phiên đó. Bản sao lưu bạn tạo trước khi xoá vẫn giữ điều đã xoá.
- Khi bạn mở cửa sổ Bio-Memory, hoặc gõ `bio-memory update`, nó hỏi trang phát hành của nó trên GitHub xem có bản mới không. Nếu có, nó tải bản mới về, kiểm tra tệp tải về, rồi cài. Yêu cầu đó không chứa gì từ trí nhớ của bạn. Việc tự cập nhật khi mở cửa sổ tắt được trong mục Riêng tư của cửa sổ.
- Bio-Memory không giữ những gì trông như mật khẩu hay khoá bí mật.

## Chưa có

- Bản Linux cho máy ARM (bản Linux hiện có cho máy x86-64, cần glibc 2.28 trở lên).
- Chữ ký số của chương trình. Bản trên Microsoft Store đang chờ duyệt.

## Giấy phép

Bio-Memory.exe dùng miễn phí, cho cá nhân lẫn công việc, theo [giấy phép Bio-Memory](LICENSE.md). Từ bản 0.11, Bio-Memory phát hành dạng chương trình đã biên dịch; mã nguồn không công bố.
Các bản đến 0.8.2rc1 đã công bố mã nguồn theo giấy phép MIT. Chúng vẫn ở các tag `v0.6.1` và `v0.8.2rc1`.

Liên hệ: contact@locaith.com · Locaith Solution Tech, Việt Nam.

---

<a id="english"></a>

## English

Bio-Memory keeps the memory of the AI you use. Tell it something today and your AI still knows it tomorrow. What is out of date is marked as
out of date, not mixed in with what is current. When you choose Forget in the Bio-Memory window, it is erased from the data file and you are
shown what the check found. When you tell your AI to forget, it is only hidden; to erase it for good, choose Forget in the window. The memory
stays on your computer, and Bio-Memory sends it nowhere by itself.

| You are | Use | Get it |
|---|---|---|
| Anyone | **Locaith Orb**, an assistant app with the memory built in | <https://orb.locaith.ai> |
| Someone using Claude Code, Claude Desktop, Codex, Gemini CLI, Cursor, Windsurf, Antigravity, VS Code or another AI app | **Bio-Memory** (Windows, macOS, Linux) | One line below, or the [latest release](https://github.com/locaith/bio-memory-ai-locaith/releases/latest) |

**Install with one line:**

| Computer | Paste this |
|---|---|
| Windows, PowerShell | `irm https://github.com/locaith/bio-memory-ai-locaith/releases/latest/download/install.ps1 \| iex` |
| Windows, cmd | `curl -fsSL https://github.com/locaith/bio-memory-ai-locaith/releases/latest/download/install.cmd -o "%TEMP%\bio-memory-install.cmd" && "%TEMP%\bio-memory-install.cmd"` |
| macOS, Linux | `curl -fsSL https://github.com/locaith/bio-memory-ai-locaith/releases/latest/download/install.sh \| sh` |

It downloads the program from the newest release on GitHub, showing the download as it goes, checks the download against the release's
SHA-256, and installs it in your own user folder: no administrator rights, no sudo. Then you have the `bio-memory` command, which walks you through the first steps: Tiếng
Việt or English; where to keep the memory (by default the `Bio-Memory` folder in your user folder); which AI apps on the computer should use
it -- Claude Code, Claude Desktop, Codex, Gemini CLI, Cursor, Windsurf, Antigravity, VS Code -- shown before anything is written, each file backed up first; if you like,
the documents in the folders you choose, read on the computer, for a business profile (the window's **Business** tab); and the window.
After that: `bio-memory open` (the window), `bio-memory status`, `bio-memory connect` (connect an AI app; `--print <name>` gives the text to paste into any other, Trae for one),
`bio-memory doctor` (check and say how to fix), `bio-memory update`,
`bio-memory uninstall` (the memory stays), `bio-memory profile` (the business profile), `bio-memory help`. `BIO_MEMORY_LANG=vi` or `en` picks the language without asking; `NO_COLOR=1`
turns colour off.

**Or download Bio-Memory.exe (Windows 10 or 11, 64-bit):**

1. Download `Bio-Memory.exe` from [Releases](https://github.com/locaith/bio-memory-ai-locaith/releases/latest).
2. Open it. The program is not signed yet, so Windows may say "Windows protected your PC". Choose **More info**, then **Run anyway**.
3. In the Bio-Memory window, choose **Install**. No administrator rights are needed: it goes into your own user folder, with an icon in Start.
4. Choose **Connect to Claude Code**, **Connect to Claude Desktop** or **Connect to Cursor**. The window shows you what it will change before
   it changes anything.
5. Open that AI app again and say: *"Please remember for me: I like black coffee, no sugar."* What it remembered shows in the Bio-Memory window.

Connecting Claude Code makes Bio-Memory the memory of **every** Claude Code project on the computer. Each project's existing notes are copied in
first. Then Claude Code's own memory is turned off, so that a session has one memory. The old note files stay as they are, and **Disconnect**
puts everything back as it was.

**Uninstall:** type `bio-memory uninstall`, or open Bio-Memory and choose **Remove Bio-Memory from this computer**. The program, its icons, the
`bio-memory` command and its connections go. Your memory in the `Bio-Memory` folder (`%USERPROFILE%\Bio-Memory` on Windows) stays where it is:
it is yours.

**Privacy:** Bio-Memory runs on your computer and sends nothing anywhere by itself. Data leaves the computer only where you connect it to
something outside. The AI you connect reads what it finds in the memory, and that part travels with your conversation to the company that makes
that AI. If you turn on the web door, or connect an online model in the advanced window, memory goes to the place you chose.
Forgetting in Bio-Memory does not erase the chat history your AI app keeps for itself. If you reopen an old session, the AI may still
see what was said in it. A backup you made before erasing still holds what you erased. When you open the Bio-Memory
window, or type `bio-memory update`, it asks its releases page on GitHub whether there is a new version; if there is, it downloads it,
checks the download and installs it. That request holds nothing of your memory; updating by itself when the window opens can be turned off
in the window's Privacy tab. Bio-Memory does not keep what looks like a password or a secret key.

**Not yet:** Linux on ARM machines (the Linux build is for x86-64, glibc 2.28 or newer); a signed program (a Microsoft Store edition is waiting for approval).

**License:** Bio-Memory.exe is free to use, personally and at work, under the [Bio-Memory License](LICENSE.md). From 0.11 on, Bio-Memory is
released as a compiled program and its source is not published. Releases up to 0.8.2rc1 were published as source under the MIT License, and
remain at the tags `v0.6.1` and `v0.8.2rc1`.

Contact: contact@locaith.com · Locaith Solution Tech, Vietnam.
