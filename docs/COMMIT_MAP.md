# Commit map — the history rewrite of 2026-09-17

On 2026-09-17 the repository's history was rewritten once, to remove a
tooling trailer line from commit messages. Nothing else changed:

- **Every commit's tree is identical.** All 485 rewritten commits were
  checked against a bundle of the pre-rewrite repository: 485 of 485 have
  the same tree object as their original, so every file at every commit is
  byte-for-byte what it was.
- **Author, committer, and both timestamps are preserved** on every commit.
  The pre-registration rule — "the protocol was committed and pushed before
  its first run" — is a statement about *order and time*, and both survive
  the rewrite; `scripts/check_preregs.py` re-derives it from the rewritten
  history and passes.
- **Only the commit identifiers changed**, because a commit's hash covers
  its message.

## Why this file exists

Documents in this repository cite commits by their short hash as the
timestamp anchor of a pre-registration or an amendment. Those written after
the rewrite, and every editable document, now cite the new hashes. The
**frozen** files — pre-registrations, gated result records, and the analysis
scripts that generate those records — are never edited after their result
exists (the reproduction and pre-registration gates enforce this), so they
still carry the *old* hashes. Resolve any old hash with the table below, or
with the full map in [`commit-map.txt`](commit-map.txt) (one line per
commit, `old<TAB>new`). The Zenodo deposit
(10.5281/zenodo.22801196) and the OSF registration index were written
before the rewrite and cite old hashes; the same map applies.

Frozen files that still cite old hashes:

- `research/analysis/PREREG_BUDGET_PARITY.md`
- `research/analysis/PREREG_WAVE4_CALIBRATED.md`
- `research/analysis/PREREG_WAVE4_DWELL.md`
- `research/analysis/PREREG_WAVE4_LIVE_PLANE.md`
- `research/analysis/RESULTS_LIVE_CHAOS_P99.md`
- `research/analysis/analysis_budget_parity.py`
- `research/analysis/analysis_layered_fix.py`
- `research/analysis/analysis_model_mismatch.py`
- `research/analysis/analysis_separation_mt.py`
- `research/analysis/analysis_separation_mt_v2.py`
- `research/analysis/analysis_separation_mt_v3.py`
- `research/analysis/analysis_trace_parity.py`
- `research/analysis/live_chaos_p99.py`

## Cited hashes, old → new

| old | new | old (full) | new (full) |
|---|---|---|---|
| `0036be3` | `e294d5a` | `0036be3ae16fd28ad7f1890b6e2fd7e37d952c17` | `e294d5afce70f28bed8ffdd7185b0707eef3cab2` |
| `01cb5ed` | `56734b0` | `01cb5ed5bc586c1f45ff83f8f3d7d78a07db08cb` | `56734b0c46ad568b397a75e09bf5072501e77b4a` |
| `077be71` | `78eb467` | `077be7193cd6834129405bcfdefbdb10d904de59` | `78eb4672875e596517326b079fd9477c92a305ae` |
| `0a61c6a` | `54dde6e` | `0a61c6a90331f4150489bb3a74dc48eca0e51b55` | `54dde6e5caa17ea599f09c4a8779f7072032185c` |
| `10febd3` | `fe2d774` | `10febd3236fef3bf851be5948e9eb2922f0c2322` | `fe2d774d29207dcae97085bda818c32d135db7de` |
| `1388a59` | `3e57552` | `1388a5911b11cc07ab1a7f428545c5249da0ee2f` | `3e5755222a38bfb679aa3f12d90d4d94b8f647c0` |
| `25c00db` | `c862be9` | `25c00db7315dd74acfed638d23dbd047917205e0` | `c862be982d709aaffd96ae2a940cf4601e123e78` |
| `264356a` | `c898b5a` | `264356a5994b1d68482d4eb6877f18ef0aa33ae8` | `c898b5aabf71ac33582848a1cafefbdfeae311ee` |
| `2bc3bf5` | `6f26034` | `2bc3bf551464d1203a1487f0550fc97357e4158e` | `6f260345eacaf86dfbe6da1ed00222951bfc0fe2` |
| `2db9c3e` | `5039f26` | `2db9c3e3f52256647ba2fa1dda6cf17f3c7049a2` | `5039f269a5a2460ec26c0607dbdee6ee4df29fe8` |
| `31c73c7` | `6f0c3d4` | `31c73c7420a8fdc4cebff092879353b099bc9ce0` | `6f0c3d4da9bd647b3c671f948b2e8e7de595958b` |
| `34ea69d` | `0000cbe` | `34ea69d6130f84c422120ef48c76e45ea6bc262c` | `0000cbe4c0203db22864dfe8a08bdac95706a24d` |
| `365ac40` | `ab5696f` | `365ac40f9ac11d0388bce50edf41927230dec0eb` | `ab5696f6db354906e1e8a4efa4a078359007a896` |
| `37d36ae` | `8e01ce6` | `37d36ae3bacdf93de4b96c85a92de0a00f8cbc7f` | `8e01ce6ebdaa308ec1563d67081e3353939a5467` |
| `38f12ff` | `38c591e` | `38f12ff4f6a3700d7502dbc5cb27f6f4809bf895` | `38c591e39820d91e7706f517e184b90c60cf246e` |
| `399f86a` | `3037dfa` | `399f86acc341a7af475752d2776ad1ba6a22ba8e` | `3037dfa7e3797746a66c2cbb25ec7535bd34e367` |
| `3e66253` | `57a8a4d` | `3e662531d79adbaa66fcf1577bcf19ae8350d212` | `57a8a4d0ea0494e914eedf5dbc6f210d00911cc3` |
| `3fc418c` | `608eb5b` | `3fc418cb9c6aa5ec1395deeac2ca784bca3aa5ae` | `608eb5bc859b67671cc20f50bcb9c281a454b4bf` |
| `43f27a6` | `3582266` | `43f27a6f581b8ddcb7c70c20eb3688a3ae1c29c8` | `358226600d5c182ffea2f6f77194a4db2ee10e5f` |
| `4e499ef` | `87bc889` | `4e499efa0cd265d716a5bddbd8e98acede5d7d30` | `87bc889c12cfbcd64fa829c2827f753feb8c14a2` |
| `51d2f65` | `0df90c3` | `51d2f65541d9fb094446344fdd6cc51a1ebc4315` | `0df90c3657d7246d3496d128549d1a07e81b4b81` |
| `527a1b0` | `0b96d70` | `527a1b06ace75e346b482fed2b5d14d4f1db1c45` | `0b96d70cc1b415f00e9f45fce4e1c9439cce1c91` |
| `573c197` | `7063081` | `573c1973752541d50114ce22e96a73beceefc18b` | `70630812d25aec7b7648d9d8f7e25018987d76c3` |
| `5c75e8a` | `04de2b8` | `5c75e8ac0052f448a90943b1bdf2eb77eb3b0885` | `04de2b8d24826c52c19c08f4b3599c8cd43cfd9c` |
| `6229b6a` | `5dab1de` | `6229b6ac741797dd410a983f099467fcd37ae684` | `5dab1de3fc5c516171220c894ced329ba34273f6` |
| `650ce29` | `f0157cd` | `650ce29838c72b765c9469a0741ded426e41c050` | `f0157cd40e93095b1ddf6971357a69c1a44cc0f3` |
| `654a765` | `0d012d4` | `654a7658c8f296729efb35762ee34ed298225cd5` | `0d012d4452daaa7d8673d61edc50dd45daeed1c5` |
| `67a32a5` | `b47b728` | `67a32a5c56f6a8672126b603554248cd6c795e75` | `b47b728f8937f6c2713210366c3bd04abb8821af` |
| `6b2de57` | `dd16920` | `6b2de57436d4dc563362d509c749015595837014` | `dd16920d03f7238cba1f254e4d1ae949a5a6204f` |
| `6ca8626` | `759de28` | `6ca86263e2306903c9b98231adbe0e7deeeed0ac` | `759de284a3a450e6d514d951288490f40b701ad3` |
| `701d29b` | `7595819` | `701d29bac3433f11421940ed4439659c1c5d852a` | `7595819176a773d598d818aba796d01f72935fe7` |
| `717f8bd` | `050ff63` | `717f8bdfb12229199e6f96f756579652ee218e4b` | `050ff63d64ebf778d2f2bda5b737932e80d9de6c` |
| `71d6ea7` | `48efa40` | `71d6ea7eff6bee3efa51dd89c9fdb5f68eaff97d` | `48efa40b333cd93ff688a2aa980c418612258d97` |
| `791f96a` | `9053f02` | `791f96aa6c5f3cd9d861e99c805b478a0258f4c0` | `9053f023126129aa3e2445c37566e22007199690` |
| `7bdbd4f` | `523422b` | `7bdbd4f891aa2d0652ec67478b2a460ee65d7303` | `523422b088a4abb62f6e90c6669d0c85d03239cd` |
| `7deb6a3` | `d34a9ce` | `7deb6a3e2ce68831775be1f462f8dceaa71f3d5c` | `d34a9ce64a623aad98c6ed7ce852df5a38368510` |
| `821274d` | `796c223` | `821274de138815cfa0283b1f08e3b0e786f46324` | `796c223ef8e77ff5136e161622b641f9df4ae01a` |
| `849c478` | `5f4e0dd` | `849c4786f2bd54b8cb90227c6a6631f7d638fa10` | `5f4e0ddeedc601ceed360ea909ba5c0a84d0f824` |
| `86c29c5` | `1d71f4f` | `86c29c57c6c117d98c4f9b0b02091aa94159503a` | `1d71f4f3535cbab82a5c110bed8cef90c0887482` |
| `896c896` | `3a7528a` | `896c8965b42913431df490fd393e8fb04fd1f78e` | `3a7528a87d49258d584d59fdf2dad5c3c68d1bfb` |
| `9081a63` | `f8596c2` | `9081a63fa762afd6af518d7d70d17dd512c91cc6` | `f8596c22eaf6160c3f3716906cf6976354c1449a` |
| `97f5879` | `0913f6b` | `97f587998a4ffa69eaf6b22ede9d2fa2e822b680` | `0913f6b908a85540dae62ab2714cf658052de924` |
| `992f8d7` | `530c9ac` | `992f8d773dcb7cd655298eec435272394cd3231b` | `530c9ac808907bfd6b543c0dffa352fe753f1b2c` |
| `99a2d5e` | `81dd3d5` | `99a2d5e9f1bcbf9d1a7fa3d99652f351f0348895` | `81dd3d5e6af6e32069bc84edd560be384d868408` |
| `9f66192` | `45785ce` | `9f66192c583b9f10be02856a06eed92fadeabd31` | `45785ce42ac0a12ebde6b5f7215c5cff21fd2f5c` |
| `a8c4098` | `8f2eb30` | `a8c4098c7edeb1115005f750bc11be0d49f893ed` | `8f2eb3052dda92d1c0b4b9253ecad31a80b4604b` |
| `acb792b` | `1ff8a0d` | `acb792b7919df46c7f6b12844304a3f06182daf7` | `1ff8a0d1d15af8dfdc34d73bd3680d31db4931d4` |
| `acec891` | `9431553` | `acec8912e0e7f348c08bafd0bdbc3bf6da913a25` | `94315533152adbecdf0c2737055b7edf63effe32` |
| `ad9e788` | `cb4f288` | `ad9e7885844260923cfd0a10ed1dd5c4e2504e49` | `cb4f2880097a27cb3dc9377c8591af49bcedce48` |
| `b09a492` | `825c349` | `b09a4928af7e9da3c215ca969208f22ff1bb0bc8` | `825c349a291b01abcc29cbb62b552907512b3b61` |
| `b2900b2` | `0e73ec1` | `b2900b2fffe9d6e5aa91b75cb95beb32f7d74d5a` | `0e73ec1326d35a40f3fed04adcb728b221b4615e` |
| `b3cd766` | `619c8d9` | `b3cd766c398fb61eebd112f8aa58d902cb3cb4c0` | `619c8d92cc70e3198fdb1303ebd4a1629a164ee9` |
| `b55f91b` | `a32374b` | `b55f91bbb376087c075f3780ecee749f50a48c15` | `a32374b2e6c0f9708fb03e0c914acf585b70047c` |
| `bec0f62` | `f719127` | `bec0f626a1315447e1d542495077670e5a7fbaad` | `f719127f7da1df6a55ba68f428479668d3218ffa` |
| `bedd6ff` | `93d50ce` | `bedd6ff3be47a2b48f5655593e687bd79647a739` | `93d50ce08c7ef722fde9fbece6590a479b904c4d` |
| `bfb70fc` | `5e0914d` | `bfb70fc320e20eb283927946a180a14e62357316` | `5e0914d2bb452b2a9a1b69943b33dc8f956e55d6` |
| `c38bce6` | `6c12921` | `c38bce6fbf33635a52fb727624d314bceeb67971` | `6c129214a7b2a2a4175e3d0fe2ddda2c010d0c74` |
| `c6a021f` | `e5ca126` | `c6a021f1d4181ff89603428c20b400608a02589c` | `e5ca1268228b3e1463811bbc265807a31fcccc2a` |
| `c7c0865` | `6956fdd` | `c7c0865ecf442cc1054437f39309827d254a7c71` | `6956fdd7f1e4bafabefd3df9899a2bcd7dc614ff` |
| `cb96dfd` | `3ff5b6d` | `cb96dfdb0fc9d11030e65547fde5991bad6c6477` | `3ff5b6d3c6a5587a54ca69ef414f1cf9a3b91f4e` |
| `cfab691` | `6cfe29f` | `cfab691bc0cdffdba8e5f657eef8c002d3df3de5` | `6cfe29f83bb3e52e5fa4f677283ffca7446f46d4` |
| `cfeca12` | `98d8153` | `cfeca1281a69bf4d7f8fa071bf3e7fbcce129ad1` | `98d815305d38c72c898abb7cf23836efd2cb7d27` |
| `d5101c1` | `ced9507` | `d5101c11015e86540f0eee1f80b31c60fe234de4` | `ced9507db194ffc01bd5a7d2f7d5c765cef5874e` |
| `e25314f` | `908cd0d` | `e25314f45168227902b2d342ba1a5557d48b022e` | `908cd0dacbdee732f2d3fe3533e1df68c9d57125` |
| `e94c3e5` | `7eaba72` | `e94c3e5072d9b754895b16faba85a7e3bf15178f` | `7eaba727261ac42997ec3a70f8ab14f195d1d77e` |
| `ec7a230` | `33bd83c` | `ec7a230a3cf728177437766ee2cdc8172d9559e1` | `33bd83c0d0b8068e8d4f6da26cf09c5b457df9c3` |
| `efd353e` | `9369f51` | `efd353edf6f41886cb092cddb5fbd0fb1309c48a` | `9369f5157d1aedbe4134bb3bb2ec82b12c626622` |
| `f045e68` | `b184aee` | `f045e68a5f922b34d4502d6a4d9ec0626e97dd0d` | `b184aeed2430be88dcd9ce31fe28bb9848450f59` |
| `f1102d6` | `78d7180` | `f1102d61c99e4ffd11acd49aeb9306903b57cae7` | `78d71803788c53b9e0450044b825b591c1965ff8` |
| `f1b2988` | `fbb92e8` | `f1b2988c75f0134d619a2a004923928ee2fd7591` | `fbb92e871162d6277bdb6f8bf4cf1cc6fc17a411` |
| `f8aa8e6` | `9c85610` | `f8aa8e69e7bb6bd24ebbfb8e460373d38ceb9d83` | `9c85610336415bc0138a5ba4c39574f1e507110f` |
| `f9da4e0` | `576254f` | `f9da4e0c454d678831ca4bbdb4788996eca160f1` | `576254fb6105ed8beab385447745ed59f683ff88` |
| `fc7e72c` | `14636fc` | `fc7e72c725ee85e56358427d2d8eff223001eda4` | `14636fc7d393368ae11ad9594afe7e7c717d7248` |
| `ff84846` | `33ab619` | `ff848463ea0e5de38c03f418347bbb6edc47503e` | `33ab6193066458a60411efe1bb5386bab793328a` |

Rewrite tool: `git filter-repo`, message callback only, `--replace-refs
delete-no-add`; the pre-rewrite bundle is retained offline by the author.
