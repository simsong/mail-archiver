# Public email validation datasets

This pipeline builds disposable validation Mailbags from nine anonymously
downloadable public sources. All generated material stays under the ignored
repository-local `data/` directory; downloaded source artifacts are retained
unchanged beside their SHA-256 source manifests.

The rationale, longitudinal comparison fields, baseline policy, and rules for
intentional behavior changes are documented in
[`doc/REGRESSION.md`](../doc/REGRESSION.md).

| Dataset | Coverage | Source form |
| --- | --- | --- |
| Enron | Complete CMU distribution | Message tree in tar.gz |
| SF-LOVERS | Complete Internet Archive item | Babyl/RMAIL in 7z |
| SpamAssassin | 6,046 ham and spam messages | Five tar.bz2 files |
| GNU emacs-devel | January 2005, 2015, and 2025 | Raw MBOX |
| IETF-822 | January 1997, September 2010, and August 2021 | Raw MBOX |
| Apache httpd-dev | January 2005, 2015, and 2025 | Raw MBOX |
| GCC | January 2005, 2015, and 2025 | gzip-compressed MBOX |
| lore linux-doc | Pinned public-inbox epoch | Git object database |
| Usenet comp.mail.mime | Complete Internet Archive group file | MBOX in ZIP |

The SpamAssassin Public Corpus is a small, openly downloadable benchmark from
the Apache SpamAssassin project. It combines ordinary mail (`easy_ham` and
`hard_ham`) with unsolicited mail (`spam`), with addresses and some identifying
material obfuscated by the corpus maintainers. It is useful for parser and
classification validation, but its early-2000s composition is not representative
of current spam.

TREC07 and W3C are deliberately absent: their official bulk-download paths are
currently unavailable or require authentication. Avocado is absent because it
is not a public anonymous-download dataset.

## Execution contract

Yes: every enabled dataset runs both locally and on AWS. The dataset definition
and processing code are shared; AWS does not have a separate ingestion
implementation. An EC2 worker checks out the configured repository ref and runs
the same `make validation-run DATASET=<id>` target used locally.

| Stage | Local mode | AWS EC2 mode |
| --- | --- | --- |
| Read dataset TOML | Repository checkout | Published repository checkout |
| Download and hash sources | `data/downloads/<id>/` | Worker-local `data/downloads/<id>/` |
| Safely extract and preprocess | Same Python pipeline | Same Python pipeline |
| Ingest with on-demand ClamAV | Same `mailarchiver` CLI | Same `mailarchiver` CLI |
| Verify Mailbag | Installed `verify_mail_archive.py` | Installed `verify_mail_archive.py` |
| Package result | `data/results/<id>.mailbag.zip` | Same worker-local ZIP |
| Retain result | Local ignored `data/` tree | Existing parameterized S3 bucket |

The operational differences are scheduling and result retention. Local
`validation-run-all` processes datasets sequentially on the current host. AWS
`validation-aws-start-all` launches one independent EC2 instance per dataset so
they can run concurrently; each uploads status, logs, its JSON report, and the
verified ZIP before terminating. A worker failure cannot cause another
dataset's instance to be reused or retained.

The local pipeline has completed an end-to-end SpamAssassin acceptance run. The
SAM template has passed build and lint validation. A live AWS acceptance run
still requires a published repository ref plus the deployment region, existing
bucket, VPC, and public subnet parameters.

## Local mode

List configurations or run one dataset:

```sh
make validation-list
make validation-run DATASET=spamassassin
```

Run every configured dataset sequentially:

```sh
make validation-run-all
```

The final ZIP is `data/results/<dataset>.mailbag.zip`; the associated JSON report
is `data/runs/<dataset>.json`. `validation-fetch` and `validation-prepare` expose
the earlier stages for investigation without ingesting.

## AWS EC2 mode

Copy `validation/samconfig.example.toml` to the ignored
`validation/samconfig.toml`, then set the existing result bucket, VPC, public
subnet, and published repository ref. Deploy and launch one dataset with:

```sh
make validation-sam-validate
make validation-sam-deploy
make validation-aws-start DATASET=spamassassin STACK=mailarchiver-validation
```

Launch all nine workers independently and concurrently:

```sh
make validation-aws-start-all STACK=mailarchiver-validation
```

SAM creates the launcher and worker IAM/network resources, but never creates or
owns the long-lived bucket. Each EC2 worker has no inbound rule, uploads beneath
`s3://<bucket>/<prefix>/<run-id>/<dataset>/`, and terminates after its EXIT trap
uploads status and logs. Cloud-init failures that occur before the AWS CLI is
installed can only be diagnosed through the EC2 console output.
