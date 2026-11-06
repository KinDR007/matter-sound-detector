# TODO: thread_sound_detector

## Další kroky
- [ ] Pod 50 % na desce (111/170, hlasitost 100 %): siren 4, door_slam 4, footsteps 1; na hraně speech, glass_break, water 5 (alarm/beep teď záměrně potlačené konkrétními třídami)
- [ ] Třídy pod 50 % i po FSD50K dev (validace): glass_break 0,40, door_slam 0,35, scream 0,49; kroky a sklo testovat přes externí reproduktor s basy
- [ ] Přehrát 10 vzorků každé události (`scripts/sound_test.py -n 10`); třídy s úspěšností detekce < 50 % přetrénovat (více dat, augmentace z trénovacích klipů přes `own_label.py --gt`, vlastní nahrávky)
- [ ] Ověřit v HA nové entity (17 událostí; zařízení je spárované z doby 11 tříd, HA může potřebovat znovu načíst zařízení)
- [ ] Entita hladiny zvuku v HA vedle událostí: Matter ani HA Matter integrace nemají cluster pro zvuk → endpoint 18 s Pressure Measurement (MeasuredValue = dB, HA zobrazí „hPa“), v HA template senzor s jednotkou dB; Leq (A-vážené z FFT) za 10 s, kalibrace `snd spl_cal <dB>` proti měřiči v telefonu
- [ ] Inference int8 145 ms: profilovat vrstvy (conv0 s 1 vstupním kanálem jde přes obecné im2col)

## Hotovo
- [x] Hierarchie alarmů v detektoru: smoke_alarm / alarm_clock / phone_ring potlačí obecné alarm / beep; na desce události rodiny 88 → 50 (ideál 50), pokrytí rodiny beze změny 44/50
- [x] Akustický test modelu z FSD50K dev: 111/170 (předtím 94/170, ale při nižší hlasitosti)
- [x] Trénink rozšířen o FSD50K dev (722k oken): validační recall 0,508 → 0,614; širší síť 2× nestíhá na desce (430–580 ms)
- [x] Oprava přetečení PDM (falešné beep/smoke_alarm při logování Matter)
- [x] Porovnání učitelů (`model/teacher_torch.py`): PANNs SED AP 0,747, YAMNet 0,674, EfficientAT na 1s oknech nepoužitelný; na desce kombinace 91/170 vs YAMNet 94/170, ponechán YAMNet
- [x] 17 tříd, výběr prahů a počtu oken simulací detekce, Matter data model se 17 endpointy, Identify cluster na všech endpointech
- [x] Int8 kvantizace (CMSIS-NN) jako výchozí: RAM −145 KB, flash −47 KB, inference 368 → 145 ms, přesnost stejná ([docs/model_int8.md](docs/model_int8.md))
- [x] Matter over Thread, 11 tříd, destilace z YAMNet
- [x] Testovací skript `scripts/sound_test.py` a GUI `test/`
- [x] Vlastní zvonek (`own_label.py`): na desce 5/6 (předtím 0/4)
