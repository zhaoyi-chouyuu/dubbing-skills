# Semantic line-break regression cases

Read the full cue and adjacent subtitle cues. Do not use video or audio. These cases are regression evidence, not keyword replacement rules.

## Required corrections

```text
わかったいいだろう
-> わかった いいだろう

挨拶はいいルールを変える
-> 挨拶はいい
   ルールを変える

行きなさい友達が
待っているわ
-> 行きなさい
   友達が待っているわ

ノーリー 聞いちゃダ
メよ!
-> ノーリー 聞いちゃダメよ!

手加減して
いるように見えます
-> 手加減している
   ように見えます

2撃耐えて
見せるだけなんだ
-> 2撃耐えて見せる
   だけなんだ

高貴な吸血鬼に
はひざまずけとな
-> 高貴な吸血鬼には
   ひざまずけとな

どっかで残飯で
もあさっていたのかよ
-> どっかで残飯でも
   あさっていたのかよ

さあまずは１回戦
-> さあまずは1回戦
```

## Negative cases

Do not split or add a space merely because a trigger word occurs:

```text
思い知るがいい
いい子にして
いい加減にしろ
わかったよ
わかったら
教室に行きなさい
```

## Decision test

Before changing a semantic space or line break, answer all of the following:

1. Is the proposed boundary between grammatical units rather than inside one?
2. Is the first line free from a misleading incomplete reading?
3. Can the second line connect without an orphaned particle, ending, or auxiliary?
4. Are names, fixed expressions, modifier-head relations, and number-counter units intact?
5. Does the change preserve every non-format character and the original timecode?

If any answer is uncertain, preserve the source and add an `人工复核` report row.
