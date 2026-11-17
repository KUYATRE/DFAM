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

예를 들어 다음 버전이 v3.01이면, 파일을 검토하고 스테이징한 뒤 다음과 같이 기록한다.

```powershell
git commit -m "Release v3.01: describe the change"
git tag -a v3.01 -m "LogPlotter v3.01"
git push --atomic origin HEAD refs/tags/v3.01
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

### v3.00 — 2026-11-17

- `Monthly Gas Usage` 버튼으로 현재 `root_dir` 아래의 공정 CSV를 재귀적으로 집계한다.
- 월 범위, 설비 및 Tube 필터, N2/O2 월별 그래프, Tube별 사용량과 설비별 합계를 제공한다.
- N2 기본 컬럼: `Low N2(PV)`, `High N2(PV)`, `POCL N2(PV)`, `Door Purge N2(PV)`.
- O2 기본 컬럼: `High O2(PV)`, `Low O2(PV)`. SP, 압력, Offset, 별도 Total 컬럼은 합산하지 않는다.
- 실측 유량을 인접 시각 사이에서 사다리꼴 적산한다.
  `ft³ = Σ[(q₀ + q₁) / 2 × 경과초 / 60] / 28316.846592`.
  월 경계에서는 유량을 선형 보간해 각 월에 나눠 반영한다.
- 결과는 원본 sccm의 표준상태를 유지한 ft³이다. 별도 온도/압력 보정은 하지 않는다.
  환산 근거: [NIST Handbook 44, Appendix C](https://www.nist.gov/system/files/documents/2024/01/14/NIST%20HB%2044%202024%20Appendix%20C%20General%20Tables%20of%20Units%20of%20Measurement.pdf).
- 설비 전체는 기록된 Tube 사용량의 합계다. 미기록 대기/퍼지, 파일 사이 공백,
  마지막 샘플 이후 사용량은 추정하지 않는다. 이 값은 별도 유틸리티 계량기 사용량과 다를 수 있다.
- 기본 60초를 넘는 기록 간격, 역행/누락 시각, 음수/비수치 유량을 제외하고 검토 내역에 표시한다.
  허용 간격과 가스 컬럼은 계산 설정에서 변경할 수 있다.
- 동일 설비/Tube/가스의 겹치는 구간은 시작 시각, 파일 경로 순으로 한 번만 집계한다.
  뒤 파일의 겹치지 않는 나머지 구간은 포함하며 중복 제외 내역을 표시한다.
- 유효 기록이 없는 값은 `—`로 표시해 실제 0 사용량과 구분한다.
  설비 합계는 집계 가능한 Tube 값을 더하며, 일부 Tube 값이 없으면 부분 집계로 표시한다.
- Excel에는 전체 설비별/Tube별 결과, 검토 내역, 읽은 파일, 계산 설정을 저장한다.
  화면 필터는 내보내기에 적용하지 않는다. 설비의 집계 시간은 Tube 시간의 합계다.
- 백그라운드 집계, 진행 표시 및 취소를 지원한다. 설정 변경 시 이전 결과는 비운다.
- 검증: 단위 환산, 불규칙 간격, 월/연도/윤년 경계, 중복/겹침, 누락 데이터,
  설비/Tube 합계, Excel, 화면 연결 및 집계 중 종료를 포함한 테스트 42개 통과.
  사용자 제공 `POCL#1` 샘플 275개를 읽고 Tube 6개의 집계와 중복 구간 처리를 확인했다.

#### 사용 순서

1. 메인 화면의 `Monthly Gas Usage`를 누른다. 현재 `root_dir`가 자동으로 들어간다.
   실제 설비의 기본 경로는 `C:\hmi\System\RecipeProcLog`이다.
2. 집계할 시작/종료 월을 고른다. 필요한 경우 `유량 컬럼 · 계산 설정 보기`에서
   실제 sccm 컬럼과 허용 기록 간격을 확인한다.
3. `집계 / 새로고침`을 누른다. 새 로그가 추가되면 같은 버튼으로 다시 집계한다.
4. 설비/Tube 필터와 두 결과 표를 확인한다. 제외되거나 겹친 기록은 `검토 내역`에서 확인한다.
5. `Excel 내보내기`로 계산 조건을 포함한 전체 결과를 저장한다.

### v2.17 — 2026-11-17

- v2.17로 복원한 소스를 최초 버전 태그의 기준으로 기록한다.
- 파일명 자연 정렬과 수정일 정렬, Step Statistics, 기준 로그 비교 기능을 포함한다.
- v2.18에서 추가했던 구간 통계와 Trends 기능은 포함하지 않는다.
- 현재 소스의 기본 로그 경로는 `C:\hmi\System` 아래의 `RecipeProcLog`,
  `AlarmHistoryLog`, `RecipeHistoryLog`이다. 기존 v2.17 실행 파일의 기본 경로와는 다를 수 있다.
- 소스, 테스트 24개, 아이콘, PyInstaller 설정, 고정된 빌드 의존성을 함께 관리한다.
- 기존 `dist`의 실행 파일은 이번 Git 등록 과정에서 다시 빌드하지 않는다.
