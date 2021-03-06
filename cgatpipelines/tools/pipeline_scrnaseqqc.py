"""===========================
Single Cell RNASeq QC
===========================

Overview
========

This pipeline takes in both Fastq and aligned reads files (BAM),
and performs a number of QC steps.  These output metrics and statitistics
that can be used to filter out problematics cells in a
single cell RNA-seq experiment.

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general
information how to use cgat pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline.yml` file.
cgatReport report requires a :file:`conf.py` and optionally a
:file:`cgatreport.yml` file (see :ref:`PipelineReporting`).

Default configuration files can be generated by executing:

   python <srcdir>/pipeline_scrnaseqqc.py config

Input files
-----------

None required except the pipeline configuration files.

Requirements
------------

The pipeline requires the results from
:doc:`pipeline_annotations`. Set the configuration variable
:py:data:`annotations_database` and :py:data:`annotations_dir`.

On top of the default cgat setup, the pipeline requires the following
software to be in the path:

.. Add any additional external requirements such as 3rd party software
   or R modules below:

Requirements:

* samtools >= 1.1
* sailfish >= 0.9.0

Pipeline output
===============

.. Describe output files of the pipeline here

Glossary
========

.. glossary::


Code
====

"""
from ruffus import follows, transform, regex, suffix, mkdir, originate, \
    add_inputs, collate, jobs_limit, formatter, merge
from ruffus.combinatorics import product
import sys
import os
import glob
import sqlite3
import cgatcore.experiment as E
from cgatcore import pipeline as P
from cgatpipelines.report import run_report

# load options from the config file
PARAMS = P.get_parameters(
    ["%s/pipeline.yml" % os.path.splitext(__file__)[0],
     "../pipeline.yml",
     "pipeline.yml"])

# add configuration values from associated pipelines
#
# 1. pipeline_annotations: any parameters will be added with the
#    prefix "annotations_". The interface will be updated with
#    "annotations_dir" to point to the absolute path names.
PARAMS.update(P.peek_parameters(
    PARAMS["annotations_dir"],
    "genesets",
    prefix="annotations_",
    update_interface=True,
    restrict_interface=True))


# if necessary, update the PARAMS dictionary in any modules file.
# e.g.:
#
# import cgatpipelines.tasks.geneset as geneset
# geneset.PARAMS = PARAMS
#
# Note that this is a hack and deprecated, better pass all
# parameters that are needed by a function explicitely.

# -----------------------------------------------
# Utility functions
def connect():
    '''utility function to connect to database.

    Use this method to connect to the pipeline database.
    Additional databases can be attached here as well.

    Returns an sqlite3 database handle.
    '''

    dbh = sqlite3.connect(PARAMS["database"])
    statement = '''ATTACH DATABASE '%s' as annotations''' % (
        PARAMS["annotations_database"])
    cc = dbh.cursor()
    cc.execute(statement)
    cc.close()

    return dbh


# ----------------------------------------------------------
try:
    PARAMS['data']
except NameError:
    DATADIR = "."
else:
    if PARAMS['data'] == 0:
        DATADIR = "."
    elif PARAMS['data'] == 1:
        DATADIR = "data.dir"
    else:
        DATADIR = PARAMS['data']

# --------------------------------------
FASTQ_SUFFIXES = ("*.fastq.1.gz",
                  "*.fastq.2.gz",
                  "*.fastq.gz")
FASTQ_DIR = PARAMS['fastq_dir']
# set to value for testing purposes (see regexes below)
if FASTQ_DIR == "?!":
    FASTQ_DIR = ""

FASTQ_FILES = tuple([os.path.join(FASTQ_DIR, suffix_name)
                     for suffix_name in FASTQ_SUFFIXES])
FASTQ_REGEX = regex(os.path.join(FASTQ_DIR, r"(\S+).fastq.1.gz"))
FASTQ_PAIR = os.path.join(FASTQ_DIR, r"\1.fastq.2.gz")
SE_REGEX = regex(os.path.join(FASTQ_DIR, r"(\S+).fastq.gz"))
GENESETS = [y for y in glob.glob(os.path.join("reference.dir/*.gtf.gz"))]


@follows(mkdir("transcripts.dir"))
@transform("%s" % PARAMS['annotations_geneset_gtf'],
           regex("reference.dir/(.+).gtf.gz"),
           r"transcripts.dir/\1.fa")
def makeRepTranscripts(infile, outfile):
    '''
    make a single representative transcript for each
    gene - put into a multi-fasta file
    '''

    genome_file = "/".join([PARAMS['genome_dir'], PARAMS['genome']])

    statement = '''
    zcat %(infile)s |
    cgat gff2fasta
    --genome-file=%(genome_file)s
    --is-gtf
    --log=%(outfile)s.log
    > %(outfile)s
    '''

    P.run(statement)


@follows(makeRepTranscripts)
@transform(makeRepTranscripts,
           regex("transcripts.dir/(.+).fa"),
           r"transcripts.dir/\1.spliced.fa")
def makeSplicedCatalog(infile, outfile):
    '''
    make fasta file of spliced transcript sequences
    '''

    statement = '''
    cgat cgat_fasta2cDNA
    --log=%(outfile)s.log
    %(infile)s
    > %(outfile)s
    '''

    P.run(statement, job_memory="16G")


@follows(makeSplicedCatalog)
@transform(makeSplicedCatalog,
           regex("transcripts.dir/(.+).spliced.fa"),
           add_inputs("%s" % PARAMS['ercc_fasta']),
           r"transcripts.dir/\1.ercc.fa")
def addSpikeIn(infiles, outfile):
    '''
    add ERCC-92 spike in fasta sequences
    '''

    infile = " ".join(infiles)

    statement = '''
    cat %(infile)s > %(outfile)s
    '''

    P.run(statement)


@follows(addSpikeIn,
         mkdir("ercc.dir"))
@transform(GENESETS,
           regex("reference.dir/(.+).gtf.gz"),
           add_inputs(r"%s" % PARAMS['ercc_gtf']),
           r"ercc.dir/\1.ercc.gtf")
def addSpikeInTranscripts(infiles, outfile):
    '''
    Add the ERCC spike in gene models to the
    reference gtf for quantification
    '''

    job_memory = "1G"
    in_gtf = infiles[0]
    ercc_gtf = infiles[1]

    statement = '''
    zcat %(in_gtf)s %(ercc_gtf)s  >
    %(outfile)s
    '''

    P.run(statement)


# MM this should be changed to use Salmon rather than
# sailfish, possibly use Tom's code for running Salmon
@follows(mkdir("sailfish_index.dir"),
         addSpikeInTranscripts)
@transform(addSpikeIn,
           regex("transcripts.dir/(.+).ercc.fa"),
           r"sailfish_index.dir/sa.bin")
def makeSailfishIndex(infile, outfile):
    '''
    Make a sailfish index file from a multi-fasta of
    spliced transcript sequences
    '''

    outdir = os.path.dirname(outfile)
    job_threads = 8
    statement = (
        "sailfish index "
        "--transcripts %(infile)s "
        "--out %(outdir)s "
        "--threads %(job_threads)i "
        "--kmerSize %(sailfish_kmer)s "
        ">& %(outfile)s.log ")
    # building human transcriptome takes a lot of memory
    P.run(statement, job_memory="unlimited")


if PARAMS['paired']:
    @follows(mkdir("tpm.dir"),
             makeSailfishIndex,
             addSpikeInTranscripts)
    @transform(FASTQ_FILES,
               FASTQ_REGEX,
               add_inputs([r"sailfish_index.dir",
                           FASTQ_PAIR,
                           addSpikeInTranscripts]),
               r"tpm.dir/\1/quant.genes.sf")
    def quantifyWithSailfish(infiles, outfile):
        '''
        Quantify gene/transcript expression with sailfish
        '''

        fastq1 = infiles[0]
        # need to check that fastq2 file exists
        # if not, run as single-end
        fastq2 = infiles[1][1]
        geneset = infiles[1][2]

        index_dir = infiles[1][0]
        out_dir = "/".join(outfile.split("/")[:-1])
        job_threads = 6
        fastqs = ",".join([fastq1, fastq2])
        job_memory = "1.5G"

        statement = '''
        cgat fastq2tpm
        --log=%(out_dir)s.log
        --program=sailfish
        --method=quant
        --paired-end
        --gene-gtf=%(geneset)s
        --index-file=%(index_dir)s
        --output-directory=%(out_dir)s
        --library-type=%(sailfish_library)s
        --threads=%(job_threads)s
        %(fastqs)s'''

        P.run(statement)

else:
    @follows(mkdir("tpm.dir"),
             makeSailfishIndex)
    @transform(FASTQ_FILES,
               SE_REGEX,
               add_inputs([makeSailfishIndex,
                           addSpikeInTranscripts]),
               r"tpm.dir/\1.tpm")
    def quantifyWithSailfish(infiles, outfile):
        '''
        Quantify gene/transcript expression with sailfish
        '''

        fastqs = infiles[0]
        geneset = infiles[1][1]
        # need to check that fastq2 file exists
        # if not, run as single-end
        index_dir = "/".join(infiles[1][0].split("/")[:-1])
        out_dir = ".".join(outfile.split(".")[:-1])
        job_threads = 8
        job_memory = "6G"

        count_file = "/".join([out_dir, "quant.sf"])

        statement = '''
        cgat fastq2tpm
        --log=%(outfile)s.log
        --program=sailfish
        --method=quant
        --gene-gtf=%(geneset)s
        --index-file=%(index_dir)s
        --output-directory=%(out_dir)s
        --library-type=%(sailfish_library)s
        --threads=%(job_threads)s
        %(fastqs)s;
        '''

        P.run(statement)


@transform(quantifyWithSailfish,
           regex("tpm.dir/(.+)/quant.genes.sf"),
           r"tpm.dir/\1.quant")
def transformSailfishOutput(infile, outfile):
    '''
    Take the standard sailfish output and place all
    files into a single directory with a common
    header section
    '''

    statement = '''cat  %(infile)s |
    awk 'BEGIN {printf("Name\\tLength\\tEffectiveLength\\tTPM\\tNumReads\\n")}
     {if(NR > 11) {print $0}}' >  %(outfile)s'''

    P.run(statement)


@merge(transformSailfishOutput,
       "sailfish_counts.tsv")
def mergeSailfish(infiles, outfile):
    '''
    Merge all raw counts from sailfish across each
    condition
    '''

    infiles = " ".join(infiles)
    statement = (
        "cgat combine_tables "
        "--cat=track "
        "--regex-filename='tpm.dir/(.+).quant' "
        "--log=%(outfile)s.log "
        "%(infiles)s "
        "| (read h; echo \"$h\"; sort -k1,2 ) "
        "> %(outfile)s")

    P.run(statement)


@jobs_limit(PARAMS.get("jobs_limit_db", 1), "db")
@transform(mergeSailfish,
           suffix(".tsv"),
           ".load")
def loadSailfish(infile, outfile):
    '''
    load Sailfish gene counts data into
    csvDB
    '''

    P.load(infile, outfile)

# ----------------------------------------------------------------#
# Handling BAM files, dedup with picard before featureCounts
# quantification. Retain multimapping reads when counting?


BAMDIR = PARAMS['bam_dir']
BAMFILES = [x for x in glob.glob(os.path.join(BAMDIR, "*.bam"))]
BAMREGEX = regex(r"([^/]+).bam$")


@follows(mkdir("dedup.dir"))
@transform(BAMFILES,
           BAMREGEX,
           r"dedup.dir/\1.dedup.bam")
def dedupBamFiles(infile, outfile):
    '''
    Use Picard MarkDuplicates to remove
    optical and sequencing duplicates
    '''

    job_memory = "5G"
    job_threads = 3

    os.environ["cgat_JAVA_OPTS"] = '''
    -Xmx%s -XX:+UseParNewGC
    -XX:UseConcMarkSweepGC''' % job_memory

    statement = '''
    picard MarkDuplicates
    INPUT=%(infile)s
    ASSUME_SORTED=true
    METRICS_FILE=%(outfile)s.stats
    OUTPUT=%(outfile)s
    VALIDATION_STRINGENCY=SILENT
    REMOVE_DUPLICATES=true
    '''

    P.run(statement)


@follows(mkdir("feature_counts.dir"))
@product(dedupBamFiles,
         formatter("(.bam)$"),
         addSpikeInTranscripts,
         formatter("(ercc.gtf)$"),
         "feature_counts.dir/"
         "{basename[0][0]}_vs_"
         "{basename[1][0]}.tsv.gz")
def buildFeatureCounts(infiles, outfile):
    '''counts reads falling into "features", which by default are genes.

    A read overlaps if at least one bp overlaps.

    Pairs and strandedness can be used to resolve reads falling into
    more than one feature. Reads that cannot be resolved to a single
    feature are ignored.

    '''

    infile, annotations = infiles

    # featureCounts cannot handle gzipped in or out files
    outfile = P.snip(outfile, ".gz")

    # -p -B specifies count fragments rather than reads, and both
    # reads must map to the feature
    if PARAMS['featurecounts_paired'] == "1":
        paired = "-p -B"
    else:
        paired = ""

    job_threads = PARAMS['featurecounts_threads']
    job_memory = "2G"

    statement = '''
    featureCounts %(featurecounts_options)s
    -T %(featurecounts_threads)s
    -s %(featurecounts_strand)s
    -a %(annotations)s
    -o %(outfile)s
    %(infile)s
    > %(outfile)s.log;
    gzip %(outfile)s
    '''

    P.run(statement)


@collate(buildFeatureCounts,
         regex("feature_counts.dir/(\S+)_vs_(\S+).tsv.gz"),
         r"feature_counts.dir/\2-feature_counts.tsv.gz")
def aggregateFeatureCounts(infiles, outfile):
    ''' build a matrix of counts with genes and tracks dimensions.
    '''

    # Use column 7 as counts. This is a possible source of bugs, the
    # column position has changed before.

    infiles = " ".join(infiles)
    statement = '''
    cgat combine_tables
    --columns=1
    --take=7
    --use-file-prefix
    --regex-filename='(\S+)_vs_\S+.tsv.gz'
    --log=%(outfile)s.log
    %(infiles)s
    | sed 's/Geneid/gene_id/'
    | sed 's/\-/\./g'
    | tee %(outfile)s.table.tsv
    | gzip > %(outfile)s '''

    P.run(statement, job_memory="16G")


@jobs_limit(PARAMS.get("jobs_limit_db", 1), "db")
@transform(aggregateFeatureCounts,
           suffix(".tsv.gz"),
           ".load")
def loadFeatureCounts(infile, outfile):
    P.load(infile, outfile, "--add-index=gene_id")


@follows(loadFeatureCounts,
         loadSailfish)
def quantify_expression():
    pass

# ----------------------------------------------------#
# fetch tables from mapping pipeline to use for QC


@follows(mkdir("stats.dir"))
@originate("stats.dir/context_stats.tsv")
def getContextStats(outfile):
    '''
    Grab the context stats table
    from the mapping pipeline database
    '''

    statement = '''
    cgat extract_stats
    --task=extract_table
    --log=%(outfile)s.log
    --database-url=%(mapping_database_url)s
    --table-name=%(mapping_context_stats)s
    > %(outfile)s
    '''

    P.run(statement)


@originate("stats.dir/alignment_stats.tsv")
def getAlignmentStats(outfile):
    '''
    Grab the alignment stats table
    from the mapping pipeline database
    '''

    statement = '''
    cgat extract_stats
    --task=extract_table
    --log=%(outfile)s.log
    --database-url=%(mapping_database_url)s
    --table-name=%(mapping_alignment_stats)s
    > %(outfile)s
    '''

    P.run(statement)


@originate("stats.dir/picard_stats.tsv")
def getPicardAlignStats(outfile):
    '''
    Grab the picard alignment stats table
    from the mapping pipeline database
    '''

    statement = '''
    cgat extract_stats
    --log=%(outfile)s.log
    --task=extract_table
    --database-url=%(mapping_database_url)s
    --table-name=%(mapping_picard_alignments)s
    > %(outfile)s
    '''

    P.run(statement)


if PARAMS['paired']:
    @originate("stats.dir/picard_insert_stats.tsv")
    def getPicardInsertStats(outfile):
        '''
        Grab the picard alignment stats table
        from the mapping pipeline database
        '''

        statement = '''
        cgat extract_stats
        --log=%(outfile)s.log
        --task=extract_table
        --database-url=%(mapping_database_url)s
        --table-name=%(mapping_picard_inserts)s
        > %(outfile)s
        '''

        P.run(statement)

else:
    def getPicardInsertStats():
        pass


@originate("stats.dir/duplication_stats.tsv")
def getDuplicationStats(outfile):
    '''
    Grab the picard duplication stats table
    from the mapping pipeline database
    '''

    statement = '''
    cgat extract_stats
    --log=%(outfile)s.log
    --task=extract_table
    --database-url=%(mapping_database_url)s
    --table-name=%(mapping_picard_dups)s
    > %(outfile)s
    '''

    P.run(statement)


@originate("stats.dir/coverage_stats.tsv")
def getCoverageStats(outfile):
    '''
    Grab the gene model coverage stats table
    from the mapping pipeline database

    This is a table in the report generated from a tracker,
    need to actually make this table ourselves to get
    5'/3' coverages
    '''

    statement = (
        "cgat extract_stats "
        "--task=extract_table "
        "--log=%(outfile)s.log "
        "--database-url=%(mapping_database_url)s "
        "--table-name=%(mapping_picard_dups)s "
        "> %(outfile)s")

    P.run(statement)


@follows(getDuplicationStats,
         getPicardAlignStats,
         getPicardInsertStats,
         getAlignmentStats,
         getContextStats,
         getCoverageStats)
@collate([getDuplicationStats,
          getPicardAlignStats,
          getPicardInsertStats,
          getAlignmentStats,
          getContextStats,
          getCoverageStats],
         regex("stats.dir/(.+)_stats.tsv"),
         r"stats.dir/QC_measures.stats")
def aggregateQcTables(infiles, outfile):
    '''
    Aggregate together all of the alignment stats
    tables.  Need to remove duplicate
    column names
    '''

    job_memory = "4G"

    infiles = " ".join(infiles)

    statement = '''
    cgat combine_tables
    --columns=1
    --skip-titles
    --log=%(outfile)s.log
    %(infiles)s
    > %(outfile)s
    '''

    P.run(statement)


@follows(aggregateQcTables)
@transform(aggregateQcTables,
           suffix(".stats"),
           ".clean")
def cleanQcTable(infile, outfile):
    '''
    Clean duplicate columns from table
    prior to database loading
    '''

    statement = '''
    cgat extract_stats
    --task=clean_table
    --log=%(outfile)s.log
    %(infile)s
    > %(outfile)s
    '''

    P.run(statement)


@jobs_limit(PARAMS.get("jobs_limit_db", 1), "db")
@follows(cleanQcTable)
@transform(cleanQcTable,
           suffix(".clean"),
           ".load")
def loadQcMeasures(infile, outfile):
    '''
    load QC measures into csvDB
    '''

    P.load(infile, outfile,
           options="--add-index=track")


@follows(getDuplicationStats,
         getPicardAlignStats,
         getPicardInsertStats,
         getAlignmentStats,
         getContextStats,
         aggregateQcTables,
         cleanQcTable,
         loadQcMeasures)
def get_mapping_stats():
    pass

# --------------------------------------------------- #
# retrieve metadata and load in to csvDB
# --------------------------------------------------- #


@jobs_limit(PARAMS.get("jobs_limit_db", 1), "db")
@transform("%s" % PARAMS['meta'],
           suffix(".tsv"),
           ".load")
def loadMetaData(infile, outfile):
    '''
    load metadata into csvDB
    '''

    P.load(infile, outfile)

# ---------------------------------------------------
# Generic pipeline tasks


@follows(get_mapping_stats,
         quantify_expression,
         loadMetaData)
def full():
    pass


# ---------------------------------------------------
# Generic pipeline tasks


@follows(mkdir("report"))
def build_report():
    '''build report from scratch.

    Any existing report will be overwritten.
    '''

    E.info("starting report build process from scratch")
    run_report(clean=True)


@follows(mkdir("report"))
def update_report():
    '''update report.

    This will update a report with any changes inside the report
    document or code. Note that updates to the data will not cause
    relevant sections to be updated. Use the cgatreport-clean utility
    first.
    '''

    E.info("updating report")
    run_report(clean=False)


@follows(update_report)
def publish_report():
    '''publish report in the cgat downloads directory.'''

    E.info("publishing report")
    P.publish_report()


def main(argv=None):
    if argv is None:
        argv = sys.argv
    P.main(argv)


if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
