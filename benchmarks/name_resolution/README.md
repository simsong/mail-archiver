# Synthetic name-resolution benchmark

This benchmark is inspired by a user-provided five-address pattern: one
organization address, one public-mail address, one academic address, one
university address, and one museum address. It is synthetic and contains no
copied names, addresses, or real domains. Every domain ends in `.test`.

The five aliases represent one pseudonymous person, **Avery Morgan**:

| Pattern | Synthetic alias |
| --- | --- |
| short initials | `am@aerostat.example.test` |
| concatenated full name | `averymorgan@post.example.test` |
| initials with numeric suffix | `am51@cambridge.example.test` |
| concatenated full name at a university | `averymorgan@redwood.example.test` |
| dotted full name at a museum | `avery.morgan@river-museum.example.test` |

The YAML corpus records observed RFC display-name evidence separately from the
expected identity. Two aliases are address-only, while the remaining aliases
have abbreviated or reordered evidence. This makes the fixture useful for
testing name assembly and alias linking without treating the current
header-only suggestion code as a resolver.

Run the benchmark through the Makefile:

```console
make benchmark-name-resolution
```

The command prints corpus counts and the exact-match score for the current
header-only baseline. A future resolver can be evaluated against the same
`expected_name` and `expected_group` labels.
