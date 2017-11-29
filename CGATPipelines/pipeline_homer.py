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
    INPUTBAMS = list(df['bamControl'].values)
    CHIPBAMS = list(df['bamReads'].values)
    TOTALBAMS = INPUTBAMS + CHIPBAMS

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

@active_if(PARAMS['homer'])
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
                   makeTagDirectory
                   -genome %(maketagdir_genome)s -checkGC
                   %(bamstrip)s/ %(samfile)s
                   &> %(bamstrip)s.makeTagInput.log;
                   touch %(bamstrip)s/%(bamstrip)s.txt'''

    P.run()


#####################################################
# makeTagDirectory ChIPs
#####################################################


@active_if(PARAMS['homer'])
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
                   makeTagDirectory
                   %(bamstrip)s/ %(samfile)s
                   -genome %(maketagdir_genome)s -checkGC
                   &> %(bamstrip)s.makeTagChip.log;
                   touch %(bamstrip)s/%(bamstrip)s.txt'''

    P.run()

@active_if(PARAMS['homer'])
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


@active_if(PARAMS['homer'])
@transform(findPeaks,
           regex("(.*)/regions.txt"),
           r"\1/\1.bed")
def bedConversion(infile, outfile):

    ''' '''

    statement = '''pos2bed.pl %(BED_options)s %(infile)s > %(outfile)s'''

    P.run()


@active_if(PARAMS['homer'])
@transform(findPeaks,
           regex("(.*)/regions.txt"),
           r"\1/annotate.txt")
def annotatePeaks(infile, outfile):

    ''' '''

    statement = '''annotatePeaks.pl %(infile)s %(annotatePeaks_genome)s &> Annotate.log > %(outfile)s'''

    P.run()


@active_if(PARAMS['homer'])
@transform(findPeaks,
           regex("(.*)/regions.txt"),
           r"\1/motifs.txt")
def findMotifs(infile, outfile):

    directory, _ = infile.split("/")

    statement = '''findMotifsGenome.pl %(infile)s %(motif_genome)s %(directory)s -size %(motif_size)i
                   &> Motif.log'''

    P.run()


@active_if(PARAMS['homer'])
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


@active_if(PARAMS['homer'])
@transform(annotatePeaksRaw,
           suffix(".peaks.txt"),
           ".diffexprs.txt")
def getDiffExprs(infile, outfile):

    statement = '''getDiffExpression.pl %(infile)s
                  %(diff_expr_options)s %(diff_expr_group)s > diffOutput.txt'''

    P.run()


# ruffus decorator is wrong but it need changing later
@active_if(PARAMS['homer'])
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


#####################################################
## This is the section where the deeptool
## functions are specified
#####################################################


@active_if(PARAMS['deeptools'])
@follows(mkdir("Coverage.dir"))
@follows(loadDesignTable)
@merge([CHIPBAMS,INPUTBAMS], "Coverage.dir/coverage_plot.tab")
def coverage_plot(infiles, outfile):

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    if PARAMS['deep_ignore_dups'] == True:
        duplicates = "--ignoreDuplicates"
    elif PARAMS['deep_ignore_dups'] == False:
        duplicates = ""
    else:
        raise ValueError('''Please set a ignore_dups value in the
                   pipeline.ini''')

    statement = '''plotCoverage -b %(infile)s
                   --plotFile Coverage.dir/coverage_plot
                   --plotTitle "coverage_plot"
                   --outRawCounts Coverage.dir/coverage_plot.tab
                   %(duplicates)s
                   --minMappingQuality %(deep_mapping_qual)s'''

    P.run()


@active_if(PARAMS['deeptools'])
@follows(mkdir("Fingerprint.dir"))
@follows(loadDesignTable)
@merge([CHIPBAMS,INPUTBAMS], "Fingerprint.dir/fingerprints_plot.tab")
def fingerprint_plot(infiles, outfile):

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    if PARAMS['deep_ignore_dups'] == True:
        duplicates = "--ignoreDuplicates"
    elif PARAMS['deep_ignore_dups'] == False:
        duplicates = ""
    else:
        raise ValueError('''Please set a ignore_dups value in the
                   pipeline.ini''')

    statement = '''plotFingerprint -b %(infile)s
                   --plotFile Fingerprint.dir/fingerprints_plot.pdf
                   --plotTitle "Fingerprints of samples"
                   --outRawCounts Fingerprint.dir/fingerprints_plot.tab
                   %(duplicates)s
                   --minMappingQuality %(deep_mapping_qual)s'''

    P.run()

@active_if(PARAMS['deeptools'])
@follows(mkdir("FragmentSize.dir"))
@follows(loadDesignTable)
@merge([CHIPBAMS,INPUTBAMS], "FragmentSize.dir/FragmentSize.png")
def fragment_size(infiles, outfile):

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    statement = '''bamPEFFragmnentSize -b %(infile)s
                   --histogram FragmentSize.dir/FragmentSize.png
                   --plotTitle "Fragment Sizes of PE samples"'''

    P.run()


@active_if(PARAMS['deep_bam_coverage'])
@active_if(PARAMS['deeptools'])
@follows(mkdir("DeepOutput.dir/bamCoverage.dir"))
@transform(TOTALBAMS, regex("(.*).bam"),
           r"DeepOutput.dir/bamCoverage.dir/\1.bw")
def bamCoverage(infiles, outfile):

    if PARAMS['deep_ignore_norm'] == 'None':
        normalise  = ''
        norm_value = ''
    else:
        normalise  = '--ignoreForNormalization '
        norm_value = PARAMS['deep_ignore_norm']

    if PARAMS['deep_extendreads'] == True:
        extend = '--extendReads'
    elif PARAMS['deep_extendreads'] == False:
        extend = ''
    else:
        raise ValueError('''Please set the extendreads to a value 0 or 1''')

    statement = '''bamCoverage --bam %(infiles)s
                   -o %(outfile)s
                   --binSize %(deep_binsize)s
                   %(normalise)s %(norm_value)s
                   %(extend)s
                   %(deep_bamcoverage_options)s'''

    P.run()


@active_if(PARAMS['deep_bam_compare'])
@active_if(PARAMS['deeptools'])
@follows(mkdir("DeepOutput.dir/bamCompare.dir"))
@transform((CHIPBAMS, INPUTBAMS),
           suffix('.bam'),
           r"DeepOutput.dir/bamCompare.dir/\1.bw")
def bamCompare(infiles, outfile):

    chipbam = infiles[0]
    inputbam = infiles[1]

    statement = '''bamCompare -b1 %(chipbam)s
                   -b2 %(inputbam)s
                   -o %(outfile)s
                   %(deep_bamcompare_options)s'''

    P.run()


@active_if(PARAMS['deeptools'])
@follows(loadDesignTable)
@follows(mkdir("Summary.dir"))
@merge([CHIPBAMS,INPUTBAMS], "Summary.dir/Bam_Summary.npz")
def multiBamSummary(infiles, outfile):

    infile = [item for sublist in infiles for item in sublist]
    infile = " ".join(infile)

    if PARAMS['deep_compare_setting'] == 'None':
        compare_set = 'bins'
        compare_region = ''
    else:
        compare_set  = 'BED-file --BED '
        compare_region = PARAMS['deep_compare_setting']

    if PARAMS['deep_ignore_dups'] == True:
        duplicates = "--ignoreDuplicates"
    elif PARAMS['deep_ignore_dups'] == False:
        duplicates = ""
    else:
        raise ValueError('''Please set a ignore_dups value in the
                   pipeline.ini''')

    statement = '''multiBamSummary %(compare_set)s %(compare_region)s
                   -b %(infile)s
                   -o %(outfile)s
                   --outRawCounts Summary.dir/Bam_Summary.tab
                   --minMappingQuality %(deep_mapping_qual)s
                   %(deep_summary_options)s'''

    P.run()

@active_if(PARAMS['deeptools'])
@merge(bamCoverage, "Summary.dir/bw_summary.npz")
def multiBwSummary(infiles, outfile):
     
    infiles = " ".join(infiles)

    if PARAMS['deep_compare_setting'] == 'None':
        compare_set = 'bins'
        compare_region = ''
    else:
        compare_set  = 'BED-file --BED '
        compare_region = PARAMS['deep_compare_setting']


    statement = '''multiBigwigSummary %(compare_set)s %(compare_region)s
                   -b %(infiles)s
                   -out %(outfile)s
                   --outRawCounts Summary.dir/Bw_Summary.tab
                   %(deep_summary_options)s'''

    P.run()

@active_if(PARAMS['deeptools'])
@follows(mkdir("plot.dir"))
@transform((multiBamSummary, multiBwSummary),
            suffix(".npz"),
            r"\1corr")

def plotCorrelation(infiles, outfile):
               
    statement = '''plotCorrelation -in %(infiles)s -o %(outfile)s
                   --corMethod %(deep_cormethod)s -p %(deep_plot)s
                   --plotFileFormat %(deep_filetype)s
                   --skipZeros %(deep_plot_options)s'''
    P.run()

@active_if(PARAMS['deeptools'])
@transform((multiBamSummary, multiBwSummary),
            suffix(".npz"),
            r"\1PCA")

def plotPCA(infiles, outfile):
               
    statement = '''plotPCA -in %(infiles)s -o %(outfile)s
                   --plotFileFormat %(deep_filetype)s
                   %(deep_plot_options)s'''
    P.run()

# Continue...do not have materials to test pipeline

@active_if(PARAMS['deeptools']

def computeMatrix(Infiles, outfile):

    statement = '''computeMatrix scale-regions -S %(deep_bwfile)s 
                   -R %(deep_bedfile)s --upstream %(deep_brslength)s
                   --downstream %(deep_arslength)s
                   %(deep_matrix_options)s'''




# ---------------------------------------------------
# Generic pipeline tasks


@follows(loadDesignTable,
         bedConversion,
         annotatePeaks,
         annotatePeaksRaw,
         getDiffExprs,
         getDiffPeaksReplicates,
         findMotifs,
         coverage_plot,
         fingerprint_plot,
         bamCompare,
         bamCoverage,
         multiBamSummary,
         multiBwSummary,
         plotCorrelation,
         plotPCA)

def full():
    pass


@follows(mkdir("Jupyter_report.dir"))
def renderJupyterReport():
    '''build Jupyter notebook report'''

    report_path = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                               'pipeline_docs',
                                               'pipeline_homer',
                                               'Jupyter_report'))

    statement = ''' cp %(report_path)s/* Jupyter_report.dir/ ; cd Jupyter_report.dir/;
                    jupyter nbconvert --ExecutePreprocessor.timeout=None --to html --execute *.ipynb;
                 '''

    P.run()


# We will implement this when the new version of multiqc is available
@follows(mkdir("MultiQC_report.dir"))
@originate("MultiQC_report.dir/multiqc_report.html")
def renderMultiqc(infile):
    '''build mulitqc report'''

    statement = '''LANG=en_GB.UTF-8 multiqc . -f;
                   mv multiqc_report.html MultiQC_report.dir/'''

    P.run()


@follows(renderJupyterReport)
def build_report():
    pass


def main(argv=None):
    if argv is None:
        argv = sys.argv
    P.main(argv)


if __name__ == "__main__":
    sys.exit(P.main(sys.argv))
