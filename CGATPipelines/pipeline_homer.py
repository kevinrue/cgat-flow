"""===========================
pipeline_homer
===========================

.. Replace the documentation below with your own description of the
   pipeline's purpose

Overview
========

This pipeline computes the word frequencies in the configuration
files :file:``pipeline.ini` and :file:`conf.py`.

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general
information how to use CGAT pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline.ini` file.
CGATReport report requires a :file:`conf.py` and optionally a
:file:`cgatreport.ini` file (see :ref:`PipelineReporting`).

Default configuration files can be generated by executing:

   python <srcdir>/pipeline_@template@.py config

Input files
-----------

None required except the pipeline configuration files.

Requirements
------------

The pipeline requires the results from
:doc:`pipeline_annotations`. Set the configuration variable
:py:data:`annotations_database` and :py:data:`annotations_dir`.

Pipeline output
===============

.. Describe output files of the pipeline here

Glossary
========

.. glossary::


Code
====

"""
from ruffus import *
import sys
import os
import CGAT.Experiment as E
import CGATPipelines.Pipeline as P
import CGATPipelines.PipelinePeakcalling as PipelinePeakcalling
import CGAT.BamTools as Bamtools

# load options from the config file
PARAMS = P.getParameters(
    ["%s/pipeline.ini" % os.path.splitext(__file__)[0],
     "../pipeline.ini",
     "pipeline.ini"])


#######################################################################
# Check for design file & Match ChIP/ATAC-Seq Bams with Inputs ########
#######################################################################

# This section checks for the design table and generates:
# 1. A dictionary, inputD, linking each input file and each of the various
#    IDR subfiles to the appropriate input, as specified in the design table
# 2. A pandas dataframe, df, containing the information from the
#    design table.
# 3. INPUTBAMS: a list of control (input) bam files to use as background for
#    peakcalling.
# 4. CHIPBAMS: a list of experimental bam files on which to call peaks on.

# if design table is missing the input and chip bams  to empty list. This gets
# round the import tests

if os.path.exists("design.tsv"):
    df, inputD = PipelinePeakcalling.readDesignTable("design.tsv",
                                                     PARAMS['IDR_poolinputs'])
    INPUTBAMS = list(set(df['bamControl'].values))
    CHIPBAMS = list(set(df['bamReads'].values))


else:
    E.warn("design.tsv is not located within the folder")
    INPUTBAMS = []
    CHIPBAMS = []


########################################################################
# Check if reads are paired end
########################################################################

if CHIPBAMS and Bamtools.isPaired(CHIPBAMS[0]) is True:
    PARAMS['paired_end'] = True
else:
    PARAMS['paired_end'] = False


#########################################################################
# Connect to database
#########################################################################

def connect():
    '''
    Setup a connection to an sqlite database
    '''

    dbh = sqlite3.connect(PARAMS['database'])
    return dbh

###########################################################################
# start of pipelined tasks
# 1) Preprocessing Steps - Filter bam files & generate bam stats
###########################################################################


@transform("design.tsv", suffix(".tsv"), ".load")
def loadDesignTable(infile, outfile):
    ''' load design.tsv to database '''
    P.load(infile, outfile)


#####################################################
# makeTagDirectory Inputs
#####################################################


@follows(loadDesignTable)
@transform(INPUTBAMS, regex("(.*).bam"),
           r"\1/\1.txt")
def makeTagDirectoryInput(infile, outfile):
    '''This will create a tag file for each bam file
    for a CHIP-seq experiment
    '''

    bamstrip = infile.strip(".bam")
    samfile = bamstrip + ".sam"

    statement = '''samtools index %(infile)s;
                   samtools view %(infile)s > %(samfile)s;
                   makeTagDirectory %(bamstrip)s/ %(samfile)s
                   &> %(bamstrip)s.makeTagInput.log;
                   touch %(bamstrip)s/%(bamstrip)s.txt'''

    P.run()


#####################################################
# makeTagDirectory ChIPs
#####################################################


@follows(loadDesignTable)
@transform(CHIPBAMS, regex("(.*).bam"),
           r"\1/\1.txt")
def makeTagDirectoryChips(infile, outfile):
    '''This will create a tag file for each bam file
    for a CHIP-seq experiment
    '''

    bamstrip = infile.strip(".bam")
    samfile = bamstrip + ".sam"

    statement = '''samtools index %(infile)s;
                   samtools view %(infile)s > %(samfile)s;
                   makeTagDirectory %(bamstrip)s/ %(samfile)s
                   &> %(bamstrip)s.makeTagChip.log;
                   touch %(bamstrip)s/%(bamstrip)s.txt'''

    P.run()


@transform((makeTagDirectoryChips),
           regex("(.*)/(.*).txt"),
           r"\1/regions.txt")
def findPeaks(infile, outfile):

    '''

    Arguments
    ---------
    infiles : string
         this is a list of tag directories
    directory: string
         This is the directory where the tag file will be placed'''

    directory = infile.strip(".txt")
    directory, _ = directory.split("/")
    bamfile = directory + ".bam"

    df_slice = df[df['bamReads'] == bamfile]
    input_bam = df_slice['bamControl'].values[0]
    input_bam = input_bam.strip(".bam")

    statement = '''findPeaks %(directory)s -style %(findpeaks_style)s -o %(findpeaks_output)s
                   %(findpeaks_options)s -i %(input_bam)s &> %(directory)s.findpeaks.log'''
    P.run()


@transform(findPeaks,
           regex("(.*)/regions.txt"),
           r"\1/\1.bed")
def bedConversion(infile, outfile):

    ''' '''

    statement = '''pos2bed.pl %(BED_options)s %(infile)s > %(outfile)s'''

    P.run()


@transform(findPeaks,
           regex("(.*)/regions.txt"),
           r"\1/annotate.txt")
def annotatePeaks(infile, outfile):

    ''' '''

    statement = '''annotatePeaks.pl %(infile)s %(annotatePeaks_genome)s &> Annotate.log > %(outfile)s'''

    P.run()


@transform(findPeaks,
           regex("(.*)/regions.txt"),
           r"\1/motifs.txt")
def findMotifs(infile, outfile):

    directory, _ = infile.split("/")

    statement = '''findMotifsGenome.pl %(infile)s %(motif_genome)s %(directory)s -size %(motif_size)i
                   &> Motif.log'''

    P.run()


@merge(makeTagDirectoryChips, "countTable.peaks.txt")
def annotatePeaksRaw(infiles, outfile):

    directories = []
    for infile in infiles:
        directory = infile.split("/")[0]
        directories.append(directory + "/")

    directories = " ".join(directories)

    statement = '''annotatePeaks.pl %(annotate_raw_region)s %(annotate_raw_genome)s
                   -d %(directories)s > countTable.peaks.txt'''

    P.run()


@transform(annotatePeaksRaw,
           suffix(".peaks.txt"),
           ".diffexprs.txt")
def getDiffExprs(infile, outfile):

    statement = '''getDiffExpression.pl %(infile)s
                  %(diff_expr_options)s %(diff_expr_group)s > diffOutput.txt'''

    P.run()


# ruffus decorator is wrong but it need changhing later
@follows(mkdir("Replicates.dir"))
@follows(makeTagDirectoryChips)
@originate("Replicates.dir/outputPeaks.txt")
def getDiffPeaksReplicates(outfile):

    replicates = set(df["Replicate"])

    for x in replicates:
        subdf = df[df["Replicate"] == x]

        bams = subdf["bamReads"].values

        bam_strip = []
        for bam in bams:
            bam = bam.strip(".bam") + "/"
            bam_strip.append(bam)

    bam_strip = " ".join(bam_strip)

    inputs = subdf["bamControl"].values

    input_strip = []
    for inp in inputs:
        inp = inp.strip(".bam") + "/"
        input_strip.append(inp)

    input_strip = " ".join(input_strip)

    statement = '''getDifferentialPeaksReplicates.pl -t %(bam_strip)s
                       -i %(input_strip)s -genome %(diff_repeats_genome)s %(diff_repeats_options)s>
                       Replicates.dir/Repeat-%(x)s.outputPeaks.txt'''

    P.run()


# ---------------------------------------------------
# Generic pipeline tasks


@follows(loadDesignTable,
         bedConversion,
         annotatePeaks,
         annotatePeaksRaw,
         getDiffExprs,
         getDiffPeaksReplicates,
         findMotifs)
def full():
    pass


def main(argv=None):
    if argv is None:
        argv = sys.argv
    P.main(argv)


if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
