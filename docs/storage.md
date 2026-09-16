# 사진 저장소

인증샷(§6.3)이 어디에 저장되는지와, 바꾸는 방법.

## 지금 상태

`STORAGE_DRIVER=local` 이 기본값이고 **설정 없이 바로 동작한다.**

```
POST /v1/uploads/presign      → { upload_url: "/uploads/blob/...", file_key }
PUT  /v1/uploads/blob/{key}   → 바이트 (인증 헤더 없음, HMAC 토큰 + 10분 만료)
GET  /v1/files/{key}          → 사진
```

파일은 `STORAGE_DIR`(기본 `var/uploads`) 아래에 `proofs/YYYY/MM/DD/{uuid}.jpg` 로 쌓인다.
`var/` 는 `.gitignore` 에 있다.

## 왜 local 이 기본인가

명세(`02-api-v1.md` §6.3)는 **파일이 서버를 거치지 않는** 쪽을 택했다. 그 판단이 맞다 —
사진 한 장이 수 MB 인데 서버가 중계하면 Railway 컨테이너의 메모리와 대역폭이 먼저 터진다.

다만 그러려면 Firebase 프로젝트와 **Blaze(종량제) 요금제**가 먼저 있어야 한다.
2024년 10월부터 새 프로젝트는 무료(Spark) 요금제로 Cloud Storage 를 켤 수 없다.

그때까지 인증샷 기능 전체를 멈춰 두는 것보다, **같은 클라이언트 계약으로** 서버가 받아 두고
나중에 환경변수 하나로 옮기는 편이 낫다고 판단했다. 모바일 코드는 어느 쪽이든 동일하다.

이번 학기 규모(3~6명 × 하루 몇 장)에서 서버 부담은 무시할 수 있다.

## local 드라이버의 한계

정직하게 적어 둔다.

| 항목 | 상태 |
| --- | --- |
| 한 건 크기 | 10MB 에서 끊는다 (`FILE_TOO_LARGE`) |
| 사용자별 총량 | **제한 없음.** presign 을 반복하면 디스크를 채울 수 있다 |
| 조회 권한 | 키의 uuid4(128비트)가 유일한 방어선. **인가가 아니다** |
| 백업 | 없음. 컨테이너를 갈아엎으면 사진도 사라진다 |

조회 권한은 Firebase 의 `?alt=media&token=` 모델과 같은 수준이다. URL 을 건네받은
사람은 그룹 밖이어도 볼 수 있다. 시범 사용에 들어가면 쿼터(Redis 카운터)와
서명 조회를 같이 올려야 한다.

**배포에서 디스크가 휘발성이면 local 을 쓰면 안 된다.** Railway 컨테이너는 재시작 때
파일시스템이 초기화된다. 볼륨을 붙이거나 Firebase 로 넘어가야 한다.

## Firebase 로 바꾸기

서버 코드는 이미 준비돼 있다. 아래 네 단계가 전부다.

### 1. 프로젝트와 버킷

1. [Firebase 콘솔](https://console.firebase.google.com) → 프로젝트 만들기
2. **Build → Storage → 시작하기**
3. 요금제 업그레이드를 요구하면 **Blaze** 로 올린다
   (종량제지만 무료 할당량 안에서는 0원. 결제 수단 등록은 필요하다)
4. 리전은 `asia-northeast3`(서울)
5. 버킷 이름을 적어 둔다 — `<프로젝트ID>.appspot.com` 또는 `.firebasestorage.app`

### 2. 서비스 계정 키

1. 콘솔 → **프로젝트 설정 → 서비스 계정**
2. **새 비공개 키 생성** → JSON 파일이 받아진다
3. 저장소 **밖**에 둔다. 예: `C:\keys\p2j-firebase.json`

> 이 파일은 버킷 전체 권한을 가진다. **절대 커밋하지 않는다.**
> `.gitignore` 에 이미 `*.json` 규칙은 없으므로 경로를 저장소 밖으로 두는 게 안전하다.

### 3. 환경변수

`.env` 에 세 줄:

```
STORAGE_DRIVER=firebase
FIREBASE_CREDENTIALS_PATH=C:/keys/p2j-firebase.json
FIREBASE_STORAGE_BUCKET=<프로젝트ID>.appspot.com
```

셋 중 하나라도 빠지면 **서버가 뜨지 않는다.** 사진을 올리려는 순간에야 503 을 보는 것보다
부팅 때 걸러 내는 편이 낫다 (`core/config.py` 의 `_firebase_needs_credentials`).

### 4. 확인

```bash
uv run uvicorn app.main:app --reload
```

`POST /v1/uploads/presign` 의 `upload_url` 이 `https://storage.googleapis.com/...` 로
시작하면 성공이다. `/uploads/blob/...` 이 나오면 아직 local 이다.

모바일은 고칠 게 없다. 절대 URL 이 오면 그대로 쓰고, 상대 경로면 API base URL 에
이어 붙인다 (`core/network/media_url.dart`).

## 보관 정책 (미결)

인증샷을 언제까지 둘지 정하지 않았다 (`pending-decisions.md` B-3).
무료 할당량 5GB 기준으로 30일 후 파일만 지우고 `proofs` 행은 남기는 안이 권장돼 있다.
배치 잡(`services/batch.py`)에 자리는 있지만 아직 비어 있다.
