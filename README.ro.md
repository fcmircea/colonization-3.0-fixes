# Colonization 3.0: reparații, muzică de pe CD și traducere în română

Reparații și adăugiri pentru **Sid Meier's Colonization**, versiunea MS-DOS 3.0
(`VICEROY.EXE`, 494.910 octeți, md5 `0f5d5b0063721fbc6aca314e5a43ddaf`).
Fiecare reparație a fost găsită prin măsurători în jocul care rulează, nu prin
presupuneri. Jocul îl aduci tu; nimic din el nu se află în acest depozit.

Un singur program de instalare, trei părți:

| parte | ce primești | ce îți trebuie |
|---|---|---|
| **1. Reparații** (mereu) | patru defecte reparate în `VICEROY.EXE` (lista de mai jos) | jocul tău DOS |
| **2. Muzică de pe CD** (la alegere) | jocul cântă piesele orchestrale de pe CD în locul melodiilor FM; cu o copie MP3 a CD-ului, și cele trei fanfare din versiunea pentru Windows | imaginea CD-ului tău Colonization (`.cue` + `.bin`); pentru MP3: ffmpeg; pentru fanfare: dosarul jocului tău pentru Windows |
| **3. Română** (la alegere) | tot jocul în română, cu iz de epocă: poveste în graiul vremii (1492–1776), meniuri în limba de azi | nimic în plus |

| reparație | ce repară |
|---|---|
| `traderoute-boycott-v2` | drumurile comerciale nu descărcau într-o colonie mărfurile aflate sub boicot |
| `traderoute-topup` | la o oprire de încărcare de mai târziu nu se încărca nimic dacă toate calele erau folosite, chiar și pe jumătate |
| `rng-idle-stir` | generatorul de numere aleatoare era repornit de la ceas de vreo 18 ori pe secundă, așa că bătăliile apropiate în timp trăgeau aproape același număr |
| `traderoute-unload-cap` | un drum comercial descărca tot într-o colonie plină și surplusul se strica; acum se oprește la capacitatea magaziei, iar restul rămâne la bord |

## Instalare

1. Descarcă `colonization-fixes-v1.0.zip` de pe pagina
   [Releases](https://github.com/fcmircea/colonization-3.0-fixes/releases) și
   dezarhivează-l oriunde.
2. Pornește programul de instalare:
   - Windows: dublu clic pe `Install.cmd` (Windows PowerShell 5.1 sau mai nou;
     nu trebuie Python).
   - Linux sau macOS: `sh install.sh` (trebuie Python 3).
3. Răspunde la întrebări: unde e dosarul `COLONIZE`, dacă vrei muzică de pe CD
   și dacă vrei româna. Enter alege varianta propusă.

Dosarul jocului tău este **doar citit**. Programul face lângă el un dosar nou
de joc, `Colonization`:

```
Colonization/
  MPS/COLONIZE     jocul tău, în engleză, cu reparațiile (salvările vin și ele)
  MPS/ROMANA       același joc în română (dacă l-ai ales)
  MPS/CD           imaginea CD-ului (dacă ai ales-o); MPS/CD/mp3: copia MP3
  Play.cmd  Setup.cmd  Romana.cmd  English.cmd     Windows
  play.sh   setup.sh   Romana.sh   English.sh      Linux, macOS
```

La prima pornire rulează configurarea: ce DOSBox folosești (DOSBox-Daum, DOSBox
Staging sau DOSBox-X; DOSBox Staging îl poate descărca singur), limba
implicită, muzica de pe CD și volumul ei, ecran întreg sau fereastră. Poți
rula oricând din nou `Setup.cmd` / `setup.sh` ca să schimbi ceva.

Pentru jocul în română, pornește `Romana.cmd` (Windows) sau `Romana.sh`
(Linux, macOS).

Ca să adaugi mai târziu o parte, rulează din nou programul de instalare: ce
există deja rămâne neatins. Ca să renunți la tot, șterge dosarul de joc. Un
joc pe care l-ai reparat înainte cu aceste programe merge: instalarea găsește
EXE-ul original în copia de siguranță (`VICEROY.EXE.0f5d5b00.bak`) sau anulează
reparațiile pe copia ei. Un EXE pe care nu-l recunoaște este refuzat și nu se
scrie nimic.

**Muzica de pe CD** vine printr-un mic driver de sunet înlocuitor (`cdshim/`):
păstrează `PSOUND.COL` original sub numele `PSOUND.FM` și cântă o piesă de pe
CD ori de câte ori jocul cere o melodie care are una. Efectele sonore și cele
câteva melodii fără piesă pe CD sună ca înainte. Merge doar dacă setarea de
sunet a jocului tău încarcă `PSOUND.COL`; fără CD, jocul sună exact ca înainte.

## Despre traducere

Toate textele pe care le vede jucătorul sunt în română: dialogurile, rapoartele,
meniurile, Colonizopedia, anunțurile și finalul. Fonturile jocului au primit
literele ă, â, î, ș, ț, și câteva rutine din EXE au fost reparate ca să scrie
corect în română: majusculele cu diacritice, ordinea cuvintelor în rapoarte,
sortarea alfabetică din Colonizopedia, tastele de meniu pe litere cu diacritice.

Numele de colonii istorice și ale Părinților Întemeietori au rămas cele
istorice; triburile au primit nume românești.

## Legal

Nu se redistribuie cod din joc. Programele de reparare modifică o copie a
`VICEROY.EXE` pe care o ai deja; muzica vine din imaginea CD-ului tău. Partea
românească din arhiva de lansare conține textul tradus și fonturi făcute din
fișierele originale; folosesc doar împreună cu propria ta copie a jocului.
Colonization este © MicroProse / 2K. Programele și documentația din acest
depozit sunt sub licența MIT (vezi `LICENSE`).
