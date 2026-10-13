# Examples

## Ballade vom Computer us Züri

🔊 [ballade-vom-computer-us-zueri.mp3](ballade-vom-computer-us-zueri.mp3) (75 s, voice `zh_frau_46000`)

A computer explains, in Zurich dialect, how this repo works.

> Grüezi mitenand, ich bin en Computer,
> ich han kei eigeti Stimm und kei Muetter.
> Drum han ich e Frau us Züri kopiert:
> sächs Sekunde Ton, und scho hät's funktioniert.
>
> Am Afang bruuch ich zwänzg Sekunde zum Lade,
> für e Schwiizer Maschine isch das würkli schade.
> D SBB wär scho z Basel mit em Kafi uf em Tisch,
> und ich frög mich no, was es «Grüezi» isch.
>
> Und seisch mir fufzäh Minute kei Wort,
> dänn mach ich Fiirabig und bin eifach fort.
> Ich schlaf wie en Beamte im Bundeshuus z Bärn
> und gib dir dis RAM zrugg, das mach ich no gärn.
>
> Mis Ohr isch es Whisper, das ghört dich ganz guet,
> doch es schribt alles Hochdütsch, das git mir e Wuet.
> Und «Chuchichäschtli» bring ich nöd über d Lippe,
> drum säg ich jedesmal, ich heig grad d Grippe.
>
> S Bankgeheimnis isch gstorbe, doch eis isch no da:
> a mis Telegram-Token chunnt würkli kein Mänsch dra.
> De Pre-Commit-Hook luegt wie en Zöllner am Rhy,
> und wer es wott pushe, dä lat er nöd ii.
>
> Kei Cloud und kei Abo, alles lauft bi dir dihei,
> din Mac wird so heiss, dass druf brätlet es Ei.
> Und gfallt dir mis Schwiizerdütsch nöd, kei Problem:
> s isch MIT-Lizänz, bau dir dis eigete System.

<details>
<summary>English translation</summary>

> Hello everyone, I'm a computer,
> I have no voice of my own and no mother.
> So I copied a woman from Zurich:
> six seconds of audio, and it worked.
>
> At first I need twenty seconds to load,
> for a Swiss machine that's a real shame.
> The SBB train would be in Basel by now, coffee on the table,
> and I'm still figuring out what "Grüezi" means.
>
> And if you don't say a word to me for fifteen minutes,
> I clock out and I'm gone.
> I sleep like a civil servant in the Federal Palace in Bern
> and give you back your RAM, that much I'm happy to do.
>
> My ear is a Whisper, it hears you just fine,
> but it writes everything in Standard German, which makes me furious.
> And "Chuchichäschtli" I just can't get past my lips,
> so every time I say I've got the flu.
>
> Bank secrecy is dead, but one thing remains:
> nobody gets anywhere near my Telegram token.
> The pre-commit hook stands guard like a customs officer on the Rhine,
> and whoever tries to push it, it won't let through.
>
> No cloud and no subscription, it all runs at your place,
> your Mac gets so hot you could fry an egg on it.
> And if you don't like my Swiss German, no problem:
> it's MIT licensed, build your own.

</details>

### How it was made

Six `speak_swiss_german` calls, one per stanza, since a few sentences per call give the cleanest takes. All with the default voice and `seed: 7`, for example:

```json
{
  "text": "Grüezi mitenand, ich bin en Computer, ich han kei eigeti Stimm und kei Muetter. Drum han ich e Frau us Züri kopiert: sächs Sekunde Ton, und scho hät's funktioniert.",
  "output_path": "/tmp/poem/1.wav",
  "seed": 7
}
```

Every take was checked with `transcribe_swiss_german`, then the six WAVs were joined with 0.8 s pauses:

```bash
ffmpeg -i 1.wav -i 2.wav -i 3.wav -i 4.wav -i 5.wav -i 6.wav -f lavfi -t 0.8 -i anullsrc=r=24000:cl=mono \
  -filter_complex "[6]asplit=5[g1][g2][g3][g4][g5];[0][g1][1][g2][2][g3][3][g4][4][g5][5]concat=n=11:v=0:a=1" \
  -c:a libmp3lame -q:a 3 ballade-vom-computer-us-zueri.mp3
```

What we learned on the way:

- **Spell out acronyms.** The model reads `SBB` and `MIT` as words, so the spoken text said `Es-Be-Be` and `Em-I-Te`.
- **Write numbers as words** in dialect (`zwänzg`, `fufzäh`), so they are spoken in dialect too.
- **English tech terms** keep a German accent. "Pre-Commit-Hook" survives, barely.
- **"Chuchichäschtli" is beyond it.** Three seeds and two spellings, no luck. So the fourth stanza owns it.
