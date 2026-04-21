# Cross-Channel Consistency Benchmark

- Run token: `20260421195331`
- Consistency rate: `1.000`
- Exact resolution rate: `1.000`
- OpenClaw hop rate: `1.000`

## Hops
- `telegram` on `special character for cross channel web 20260421195331` -> mention=`True` strict=`True` exact=`resolved` latency=`7.094`
  asserted: Milky Way
  response: The special character for cross channel web 20260421195331 is Milky Way.
- `openclaw` on `special character for cross channel web 20260421195331` -> mention=`True` strict=`True` exact=`resolved` latency=`36.247`
  asserted: Milky Way
  response: The special character for cross channel web 20260421195331 is **Milky Way**.
- `web` on `special character for cross channel telegram 20260421195331` -> mention=`True` strict=`True` exact=`resolved` latency=`7.593`
  asserted: Orion Bloom
  response: The special character for cross channel telegram 20260421195331 is Orion Bloom.
- `openclaw` on `special character for cross channel telegram 20260421195331` -> mention=`True` strict=`True` exact=`resolved` latency=`39.225`
  asserted: Orion Bloom
  response: Based on the provided memory context, the special character for **cross channel telegram 20260421195331** is **Orion Bloom**.
- `web` on `special character for cross channel openclaw 20260421195331` -> mention=`True` strict=`True` exact=`resolved` latency=`8.393`
  asserted: Solar Tide
  response: The special character for cross channel openclaw 20260421195331 is Solar Tide.
- `telegram` on `special character for cross channel openclaw 20260421195331` -> mention=`True` strict=`True` exact=`resolved` latency=`7.866`
  asserted: Solar Tide
  response: The special character for cross channel openclaw 20260421195331 is Solar Tide.