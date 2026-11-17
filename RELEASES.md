# DFAM / LogPlotter 버전 관리

완성된 버전마다 소스 커밋과 annotated tag를 GitHub에 함께 올린다.
현재 작업 브랜치는 `experiment/update_beta_v0.0.1`이다.
버전 태그를 기준으로 소스와 빌드 설정을 다시 가져올 수 있다.

## 개발 환경

Windows / Python 3.14에서 다음 순서로 준비한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py
```

실행 파일을 만들려면 다음 명령을 사용한다.

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm CsvPlotApp.spec
```

결과는 `dist/LogPlotter_v<버전>.exe`에 생성된다. 실행 파일, 빌드 중간 파일,
로그와 실제 공정 데이터는 Git에 넣지 않는다. 빌드용 아이콘과 의존성은 Git으로 관리한다.

## 버전을 완료할 때

1. 원격 상태와 기존 태그를 확인하고 새 버전을 정한다.
2. `CsvPlotApp.spec`의 `name`과 이 문서의 변경 이력을 갱신한다.
3. 테스트를 실행하고 필요한 경우 실행 파일을 빌드해 확인한다.
4. `git diff`와 스테이징 내역을 검토하고 관련 파일만 커밋한다.
5. 같은 버전의 annotated tag를 만든 뒤 브랜치와 태그를 함께 푸시한다.
6. 원격 브랜치와 태그가 해당 커밋을 가리키는지 확인한다.

예를 들어 다음 버전이 v2.18이면, 파일을 검토하고 스테이징한 뒤 다음과 같이 기록한다.

```powershell
git commit -m "Release v2.18: describe the change"
git tag -a v2.18 -m "LogPlotter v2.18"
git push --atomic origin HEAD refs/tags/v2.18
```

이미 공개한 태그는 수정하지 않고 새 버전으로 기록한다.
원격 업로드가 실패하면 원인을 해결한 뒤 동일한 커밋과 태그를 다시 푸시한다.

## 과거 버전 확인

현재 작업을 보존하면서 v2.17을 별도 폴더에서 확인할 수 있다.

```powershell
git fetch origin --tags
git worktree add --detach ../DFAM-v2.17 v2.17
```

## 변경 이력

### v2.17 — 2026-11-17

- v2.17로 복원한 소스를 최초 버전 태그의 기준으로 기록한다.
- 파일명 자연 정렬과 수정일 정렬, Step Statistics, 기준 로그 비교 기능을 포함한다.
- v2.18에서 추가했던 구간 통계와 Trends 기능은 포함하지 않는다.
- 현재 소스의 기본 로그 경로는 `C:\hmi\System` 아래의 `RecipeProcLog`,
  `AlarmHistoryLog`, `RecipeHistoryLog`이다. 기존 v2.17 실행 파일의 기본 경로와는 다를 수 있다.
- 소스, 테스트 24개, 아이콘, PyInstaller 설정, 고정된 빌드 의존성을 함께 관리한다.
- 기존 `dist`의 실행 파일은 이번 Git 등록 과정에서 다시 빌드하지 않는다.
