# Execute pinned, unmodified ricu interval callbacks on the public-demo rows
# prepared by validate_vasopressor_events.py. This is NOT load_concepts():
# dictionary range limits, weight imputation, and score aggregation are excluded.
# Usage: Rscript scripts/compare_ricu_vasopressors.R RICU_CHECKOUT WORK_DIR REPORT_JSON
args <- commandArgs(trailingOnly=TRUE)
stopifnot(length(args) == 3L)
reference <- normalizePath(args[[1]])
work <- normalizePath(args[[2]])
report_path <- args[[3]]
sha <- system2('git', c('-C', shQuote(reference), 'rev-parse', 'HEAD'), stdout=TRUE)
stopifnot(sha == 'c0efac720dbe72caaf63b361b6978a24383835bf')
suppressPackageStartupMessages({
  library(data.table); library(assertthat); library(vctrs); library(rlang); library(jsonlite)
})
# Source the unchanged computation helpers. tbl-base has an optional prt-dependent
# top-level print formatter; only its function definitions are needed here.
for (f in c('utils-misc.R','assertions.R','utils-cli.R','tbl-class.R','tbl-utils.R',
            'utils-ts.R','callback-itm.R')) source(file.path(reference,'R',f))
for (e in parse(file.path(reference,'R','tbl-base.R'))) {
  if (is.call(e) && identical(e[[1]], as.name('<-')) && is.call(e[[3]]) &&
      identical(e[[3]][[1]], as.name('function'))) eval(e)
}
manifest <- jsonlite::fromJSON(file.path(work,'ricu-manifest.json'), simplifyVector=FALSE)
results <- list()
for (entry in manifest) {
  d <- fread(file.path(work,paste0(entry$name,'.csv')), na.strings='')
  if (nrow(d) == 0L) {
    results[[entry$name]] <- list(input_rows=0L, expanded_rows=0L)
    next
  }
  d$start <- mins(as.numeric(d$start))
  d$end <- mins(as.numeric(d$end))
  x <- as_ts_tbl(d, id_vars='stay', index_var='start', interval=mins(1))
  callback <- if (entry$kind == 'mv') mimic_rate_mv else mimic_rate_cv
  out <- callback(x, val_var=c('rate','reference_row'), unit_var='rateuom',
                  stop_var='end', grp_var='linkorderid')
  # Every emitted grid value must equal the corresponding source rate. These
  # callbacks expand intervals; they do not change numeric rates or classify stops.
  values <- as.data.table(out)[, list(rate=first(rate), values=uniqueN(rate)), by='reference_row']
  expected <- as.data.table(d)[, c('reference_row','rate'), with=FALSE]
  compared <- merge(values,expected,by='reference_row',suffixes=c('','_source'))
  stopifnot(all(compared$values == 1L), all(compared$rate == compared$rate_source))
  fwrite(values,file.path(work,paste0(entry$name,'-ricu.csv')))
  results[[entry$name]] <- list(input_rows=nrow(d), expanded_rows=nrow(out),
    represented_source_rows=nrow(compared), callback=if (entry$kind == 'mv') 'mimic_rate_mv' else 'mimic_rate_cv',
    all_expanded_values_equal_source=TRUE, dictionary_callback=entry$dictionary_callback)
}
report <- jsonlite::fromJSON(report_path, simplifyVector=FALSE)
report$ricu <- list(commit=sha, execution='Unmodified mimic_rate_mv/mimic_rate_cv in R; one-minute interval',
  scope='Interval callbacks only. Raw native units retained. Dictionary unit/range filtering, weight imputation and load_concepts aggregation are not run.',
  results=results)
jsonlite::write_json(report,report_path,pretty=TRUE,auto_unbox=TRUE,digits=NA,null='null')
cat('Executed ricu callbacks for',length(results),'source groups.\n')
