# 라이브 아카이버 (yt-dlp GUI)

**링크만 붙여넣고 "시작"** 누르면 영상을 아카이빙하는 브라우저 GUI입니다.
다운로드 엔진은 [yt-dlp](https://github.com/yt-dlp/yt-dlp)를 사용해서 **YouTube 라이브뿐 아니라
일반 영상과 트위치·치지직·X(트위터)·Vimeo 등 1700여 개 사이트**를 지원합니다.

> 처음엔 [ytarchive](https://github.com/Kethsar/ytarchive)로 만들려 했으나, 현재 유튜브의
> **PO Token 정책** 때문에 ytarchive 정식 릴리스(v0.5.0)는 모든 영상 조각이 403으로 막힙니다.
> 그래서 같은 GUI는 그대로 두고 엔진만 yt-dlp로 교체했습니다. (`--live-from-start`로 라이브를
> 처음부터 받습니다.)

## 실행

**Windows**
```
시작.bat  더블클릭
```
**Mac / Linux**
```
chmod +x start.sh      # 최초 1회
./start.sh
```
공통 (어느 OS든):
```
python server.py        # 또는 python3 server.py
```
브라우저가 자동으로 `http://127.0.0.1:8731/` 을 엽니다.

## 첫 실행

처음 실행하면 필요한 도구(**yt-dlp**, **ffmpeg**)를 **현재 OS에 맞게** 자동으로 내려받습니다.
완료되면 입력창이 활성화됩니다. (이미 시스템에 `yt-dlp`/`ffmpeg`가 설치돼 PATH에 있으면 그걸 우선 사용)

- 바이너리: `bin/` 폴더 (Windows는 `*.exe`, Mac/Linux는 확장자 없음)
- 녹화 결과: `recordings/`

### 플랫폼 지원

| OS | yt-dlp | ffmpeg |
|----|--------|--------|
| Windows x64 | 공식 릴리스 | gyan.dev |
| macOS (Intel/Apple Silicon) | universal 바이너리 | martin-riedl (x86_64) |
| Linux x86_64 / arm64 | 공식 릴리스 | martin-riedl |

> **Apple Silicon(M1~) 참고:** 내려받는 ffmpeg가 x86_64 빌드라 **Rosetta 2**가 필요할 수 있습니다.
> 없으면 `softwareupdate --install-rosetta`로 설치하거나, `brew install ffmpeg`로 네이티브 ffmpeg를
> 깔아두면 그걸 우선 사용합니다.
>
> **macOS Gatekeeper:** "확인되지 않은 개발자" 차단이 뜨면
> `xattr -dr com.apple.quarantine bin` 실행 후 다시 시작하세요.

## 지원하는 링크

yt-dlp가 지원하는 **약 1700개 사이트**를 받을 수 있습니다.

| 종류 | 지원 | 비고 |
|------|------|------|
| YouTube 라이브 | ✅ | 방송 **처음부터** 받음 (`--live-from-start`) |
| YouTube 예약 방송 | ✅ | 시작 시각까지 자동 대기 |
| YouTube 일반 영상 / VOD / Shorts | ✅ | 그냥 받음 |
| 트위치 · 치지직 · SOOP(아프리카) · X · Vimeo 등 | ✅ | VOD·라이브 모두 (사이트별 차이 있음) |

> **다른 사이트에서의 주의점**
> - `--live-from-start`(처음부터 받기)는 **YouTube 전용**입니다. 다른 라이브 사이트는 보통
>   **"시작 누른 시점부터"** 녹화됩니다.
> - **■ 정지·저장**의 조각 복구는 YouTube 라이브(DASH) 기준으로 만들어졌습니다. 다른 사이트나
>   HLS 스트림은 중간에 멈추면 복구가 안 될 수 있어요. **끝까지 받으면** 어디서나 정상 저장됩니다.
> - 로그인/멤버십/지역제한 콘텐츠는 고급 옵션의 **브라우저 쿠키**가 필요할 수 있습니다.

## 사용법

1. 라이브·영상 링크 붙여넣기
2. 화질 선택 (기본: 최고 화질)
3. **▶ 시작**

- **라이브를 처음부터** 받습니다 (`--live-from-start`). 방송이 한참 진행 중이어도 시작 시점부터가
  아니라 **방송 맨 처음부터** 통째로 아카이빙됩니다.
- **예약 방송**도 링크만 넣어두면 시작 시각까지 자동 대기 후 녹화합니다.
- 여러 방송을 **동시에** 녹화할 수 있습니다.
- **■ 정지·저장**: 녹화를 멈추되, 지금까지 받은 조각을 이어붙여 재생 가능한 **mp4로 합쳐 저장**합니다.
- **로그** 버튼으로 실시간 진행 상황을 확인합니다.

## 고급 옵션

| 옵션 | 설명 |
|------|------|
| 예약/대기 방송 대기 | 시작 전 방송도 기다렸다 자동 녹화 (`--wait-for-video`) |
| 다운로드 스레드 | 조각 동시 다운로드 수 (`-N`, 기본 4) |
| 재확인 주기 | 예약 방송 상태 재확인 간격(초) |
| 파일명 템플릿 | `%(title)s`, `%(id)s`, `%(upload_date)s`, `%(channel)s`, `%(ext)s` 등 |
| 브라우저 쿠키 | 멤버십/로그인 전용 방송 시 Chrome/Edge/Whale 등에서 쿠키 자동 사용 (`--cookies-from-browser`) |
| 쿠키 파일 | Netscape 형식 cookies.txt 직접 지정 |

## 동작 방식 / 참고

- 영상+음성을 받아 **mp4로 합칩니다**(ffmpeg). 썸네일·메타데이터도 포함합니다.
- 방송이 끝나면 yt-dlp가 자동으로 합쳐 **완료** 상태가 됩니다.
- **정지 시**: yt-dlp는 라이브 조각을 마지막에 한 번에 합치는 구조라, 중간에 멈추면 조각 파일만
  남습니다. 이 GUI는 정지 시 남은 조각(`*.part-FragN`)을 영상 ID 기준으로 이어붙여 ffmpeg로
  재생 가능한 mp4를 만들고, 중간 파일은 자동 정리합니다.
- 멤버십/지역제한 방송은 "브라우저 쿠키" 또는 쿠키 파일이 필요할 수 있습니다.
- yt-dlp가 가끔 "No supported JavaScript runtime" 경고를 띄울 수 있는데, 대부분의 라이브는
  그대로 잘 받힙니다. 일부 영상에서 포맷이 누락되면 [Deno 설치](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
  후 사용하세요.

## 파일 구성

```
server.py        로컬 웹 서버(표준 라이브러리만 사용) + 작업/복구 로직
static/          브라우저 UI (index.html, app.js, style.css)
bin/             yt-dlp.exe, ffmpeg.exe (자동 다운로드)
recordings/      녹화 결과물
시작.bat         실행 런처
```
