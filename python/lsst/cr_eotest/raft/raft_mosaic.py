"""
Code to perform raft-level mosaicking from single sensor frames
compliant with LCA-13501.
"""
from __future__ import absolute_import, print_function
import os
import copy
from collections import defaultdict
import numpy as np
import astropy.io.fits as fits
import matplotlib
import matplotlib.pyplot as plt
import lsst.cr_eotest.image_utils as imutils
import lsst.cr_eotest.sensor as sensorTest
from lsst.cr_eotest.sensor.EOTestPlots import cmap_range

__all__ = ['RaftMosaic']


class RaftMosaic(object):
    """
    Raft level mosaic of individual CCDs.
    """

    def __init__(self, fits_files, gains=None, bias_subtract=True,
                 nx=12700, ny=12700, nx_segments=8, ny_segments=2,
                 segment_processor=None):
        """
        Constructor.

        Parameters
        ----------
        fits_files : dict
            Dictionary of single sensor FITS files, keyed by raft slot
            name.  These files should conform to LCA-13501.
        gains : dict, optional
            Dictionary (keyed by slot name) of dictionaries (one per
            FITS file) of system gain values for each amp.  Default:
            None (i.e., do not apply gain correction).
        bias_subtract : bool, optional
            Flag do to a bias subtraction based on the serial overscan.
            Default: True
        nx : int, optional
            Number of pixels in the x (serial) direction.  Default: 12700
        ny : int, optional
            Number of pixels in the y (parallel) direction.  Default: 12700
        nx_segments : int, optional
            Number of segments in the x (serial) direction.  Default: 8
        ny_segments : int, optional
            Number of pixels in the y (parallel) direction.  Default: 2
        segment_processor : function, optional
            Function to apply to pixel data in each segment. If None (default),
            then set do the standard bias subtraction and gain correction.
        """
        self.fits_files = fits_files
        with fits.open(list(fits_files.values())[0]) as hdu_list:
            self.raft_name = hdu_list[0].header['RAFTNAME']
            self.wl = hdu_list[0].header['MONOWL']
        self.image_array = np.zeros((nx, ny), dtype=np.float32)
        self.nx = nx
        self.ny = ny
        self.nx_segments = nx_segments
        self.ny_segments = ny_segments
        self.segment_processor = segment_processor
        self._amp_coords = defaultdict(dict)
        print("mosaic : gains = ",gains)
        if gains is None:
            # Assume unit gain for all amplifiers.
            unit_gains = dict([(i, 1) for i in range(1, 17)])
            gains = dict([(slot, unit_gains) for slot in fits_files])
        for slot, filename in list(fits_files.items()):
            #print("processing", os.path.basename(filename))
            ccd = sensorTest.MaskedCCD(filename)
            with fits.open(filename) as hdu_list:
                for amp, hdu in zip(ccd, hdu_list[1:]):
                    print("mosaic : amp = ",amp," slot = ",slot," hdu = ",hdu)
                    self._set_segment(slot, ccd, amp, hdu, gains[slot][amp],
                                      bias_subtract)

    def _set_segment(self, slot, ccd, amp, hdu, amp_gain, bias_subtract):
        """
        Set the pixel values in the mosaic from the segment values.
        """
        # Get the trimmed masked image, with or without bias subtraction.
        if bias_subtract:
            mi = ccd.unbiased_and_trimmed_image(amp)
        else:
            mi = ccd[amp].Factory(ccd[amp], ccd.amp_geom.imaging)
        # Apply gain correction.
        seg_array = np.array(amp_gain*copy.deepcopy(mi.getImage().getArray()),
                             dtype=np.float32)
        # Determine flip in serial direction based on 1, 1 element of
        # transformation matrix.
        if hdu.header['PC1_1Q'] < 0:
            seg_array = seg_array[:, ::-1]
            xmax = int(hdu.header['CRVAL1Q'])
            xmin = xmax - ccd.amp_geom.nx
        else:
            xmin = int(hdu.header['CRVAL1Q'])
            xmax = xmin + ccd.amp_geom.nx
        # Determine flip in parallel direction based on 2, 2 element
        # of transformation matrix.
        if hdu.header['PC2_2Q'] < 0:
            seg_array = seg_array[::-1, :]
            ymax = int(hdu.header['CRVAL2Q'])
            ymin = ymax - ccd.amp_geom.ny
        else:
            ymin = int(hdu.header['CRVAL2Q'])
            ymax = ymin + ccd.amp_geom.ny
        # Save coordinates of segment for later use.
        self._amp_coords[slot][amp] = xmin, xmax, ymin, ymax

        # Write the segment pixel values into the full raft image mosaic.
        if self.segment_processor is None:
            self.image_array[ymin:ymax, xmin:xmax] = seg_array
        else:
            xy_bounds = (xmin, xmax, ymin, ymax)
            self.image_array[ymin:ymax, xmin:xmax] = \
                self.segment_processor(slot, ccd, amp, xy_bounds=xy_bounds)

    def plot(self, title=None, cmap=plt.cm.hot, nsig=5, figsize=(10, 10),
             binsize=10, flipx=True, textcolor='c', annotation=''):
        """
        Render the raft mosaic.

        Parameters
        ----------
        title : str, optional
            The plot title. If None (default), then build the title
            from the RAFTNAME and MONOWL primary header keyword values.
        cmap : matplotlib.colors.Colormap, optional
            The color map to use. Default: matplotlib.pyplot.cm.hot.
        nsig : float, optional
            The n-sigma value for the sigma clipping used to determine
            the pixel value range over which the color map is mapped.
        figsize : (float, float), optional
            The width x height size of the figure in inches. Default: (10, 10).
        binsize : int, optional
            Rebin the plotted image data by binsize*binsize,
            averging over the coarser bin.  Default: 10
        flipx : bool, optional
            Flip full raft mosaic in x so that parity of image matches
            LCA-13381. Default: True
        textcolor : str, optional
            Color of the text for the segment and sensor labeling.
            Default: 'c' (cyan)
        annotation : str, optional
            Description of the plot, e.g., pixel units (ADU or e-),
            gain-corrected, bias-subtracted.  Default: ''
        """
        plt.rcParams['figure.figsize'] = figsize
        fig = plt.figure()
        ax = fig.add_subplot(1, 1, 1)
        output_array = imutils.rebin_array(self.image_array, binsize,
                                           use_mean=True)
        if flipx:
            output_array = output_array[:, ::-1]
        image = ax.imshow(output_array, interpolation='nearest', cmap=cmap)
        # Set range and normalization of color map based on sigma-clip
        # of pixel values.
        vmin, vmax = cmap_range(output_array, nsig=nsig)
        norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)
        image.set_norm(norm)
        if title is None:
            title = "%s, %i nm" % (self.raft_name, self.wl)
        ax.set_title(title)
        fig.colorbar(image)
        # Turn off ticks and tick labels for x- and y-axes.
        plt.tick_params(axis='both', which='both',
                        top='off', bottom='off', left='off', right='off',
                        labelbottom='off', labelleft='off')
        # Label segments by sensor bay and segment number.
        for slot in self.fits_files:
            seg_coords = list(self._amp_coords[slot].values())[-8]
            xmin, xmax, ymin, ymax = seg_coords
            xx = float(xmax + xmin)/2./float(self.nx)
            if flipx:
                xx = 1 - xx
            yy = 1. - (float(ymax - ymin)*0.05 + ymin)/float(self.ny)
            plt.annotate('%s' % slot,
                         (xx, yy), xycoords='axes fraction',
                         size='x-small', horizontalalignment='center',
                         verticalalignment='center', color=textcolor)
            for amp, seg_coords in list(self._amp_coords[slot].items()):
                xmin, xmax, ymin, ymax = seg_coords
                xx = float(xmax + xmin)/2./float(self.nx)
                if flipx:
                    xx = 1. - xx
                if amp <= 8:
                    yy = 1. - (float(ymax - ymin)*0.85 + ymin)/float(self.ny)
                else:
                    yy = 1. - (float(ymax - ymin)*0.15 + ymin)/float(self.ny)
                plt.annotate('%s' % imutils.channelIds[amp],
                             (xx, yy), xycoords='axes fraction',
                             size='x-small', horizontalalignment='center',
                             verticalalignment='center', color=textcolor)
        plt.annotate(annotation, (1, -0.1), xycoords='axes fraction',
                     horizontalalignment='right', verticalalignment='bottom')
        return fig
