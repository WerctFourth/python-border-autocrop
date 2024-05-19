from PIL import Image, ImageChops, ImageCms
import numpy
import os, pathlib, multiprocessing, subprocess, logging, argparse, io
ver = "R4"
    
def getAvifCmdline(argInPath: pathlib.Path, argOutPath: pathlib.Path, argPBlock) -> list:
    tmpList = list()
    tmpList.append(argPBlock.avifexecutablepath.as_posix())
    tmpList.append("-s")
    tmpList.append("3")
    tmpList.append("-q")
    tmpList.append(str(argPBlock.encodequality))
    tmpList.append("-j")
    tmpList.append("1")
    tmpList.append(argInPath.as_posix())
    tmpList.append(argOutPath.as_posix())
    return tmpList

def getParameterBlock():
    parser = argparse.ArgumentParser(description=f"Autocrop script {ver}")
    parser.add_argument("-i", "--input", type=pathlib.Path, help="Input file or directory", required=True)
    parser.add_argument("-o", "--output", type=pathlib.Path, help="Output directory template", required=True)
    parser.add_argument("-fr", "--fillratio", default=0.4, type=float, help="Border fill ratio in %% (0.0 to 100.0, default=0.4)")
    parser.add_argument("-cd", "--colordistance", default=16, type=int, help="How many colors to treat as a background color (0 to 255, default=16)")
    parser.add_argument("-et", "--exhaustivethreshold", default=-1, type=float, help="How much of a border must be cropped to not trigger exhaustive pass in %% (0.0 to 100.0, default=-1, disabled=-1, always=0)")
    parser.add_argument("-rct", "--rgbcolorthreshold", default=9, type=int, help="How much difference between bands is allowed to save a RGB image as grayscale (0 to 255, default=9, always=255)")
    parser.add_argument("-rf", "--resizefit", action="store_true", help="Enable fit resizing")
    parser.add_argument("-rw", "--resizewidth", default=0, type=int, help="Target width for fit resizing")
    parser.add_argument("-rh", "--resizeheight", default=0, type=int, help="Target height for fit resizing")
    parser.add_argument("-rfw", "--resizefitwidth", action="store_true", help="Enable width fit resizing")
    parser.add_argument("-vrt", "--verticalresizetarget", default=1200, type=int, help="Width resize target for vertical images (default=1200)")
    parser.add_argument("-hrt", "--horizontalresizetarget", default=1920, type=int, help="Width resize target for horizontal images (default=1920)")
    parser.add_argument("-ea", "--encodeavif", action="store_true", help="Enable encoding to AVIF")
    parser.add_argument("-aep", "--avifexecutablepath", type=pathlib.Path, default="./avifenc", help="Path to Avifenc executable (default=script directory)")
    parser.add_argument("-eq", "--encodequality", default=54, type=int, help="Encode quality (0 to 100, default=54)")
    parser.add_argument("-pcl", "--pngcompressionlevel", default=1, type=int, help="PNG compression level (0 to 9, default=1)")
    parser.add_argument("-lp", "--logpath", default="./autocroplog.txt", type=pathlib.Path, help="Log file path (default=script directory/autocroplog.txt)")
    parser.add_argument("-nw", "--nowait", action="store_true", help="Do not wait for a key press after completion")
    args = parser.parse_args()

    if args.fillratio < 0 or args.fillratio > 100:
        print("fillratio must be 0.0 to 100.0")
        exit()
    if args.colordistance < 0 or args.colordistance > 255:
        print("colordistance must be 0 to 255")
        exit()
    if args.exhaustivethreshold < -1 or args.exhaustivethreshold > 100:
        print("exhaustivethreshold must be -1 to 100.0")
        exit()
    if args.rgbcolorthreshold < 0 or args.rgbcolorthreshold > 255:
        print("rgbcolorthreshold must be 0 to 255")
        exit()    
    if args.pngcompressionlevel < 0 or args.pngcompressionlevel > 9:
        print("pngcompressionlevel must be 0 to 9")
        exit()
    if not pathlib.Path.exists(args.input):
        print("input file or directory must exist")
        exit()
    if args.encodeavif and not pathlib.Path.exists(args.avifexecutablepath):
        print("avifexecutablepath must point to valid avifenc")
        exit()
    if args.encodeavif and (args.encodequality < 0 or args.encodequality > 100):
        print("encodequality must be 0 to 100")
        exit()
    if args.resizefit and args.resizefitwidth:
        print("resizefit and resizefitwidth can't be used simultaneously")
        exit()    
    if args.resizefit and (args.resizewidth <= 0 or args.resizeheight <= 0):
        print("For resizefit: resizewidth and resizeheight must be set and >0")
        exit()
    if args.resizefitwidth and (args.verticalresizetarget <= 0 or args.horizontalresizetarget <= 0):
        print("For resizefitwidth: verticalresizetarget and horizontalresizetarget must be set and >0")
        exit()    
    return args

def savePng(argImg: Image, argPath: pathlib.Path, argPBlock):
    if argImg.mode == "1" or argImg.mode == "L" or argImg.mode == "LA" or argImg.mode == "P":
        argImg.save(argPath, "png", compress_level=argPBlock.pngcompressionlevel)
    else:
        if checkColor(argImg):
            if argImg.mode == "RGB" or argImg.mode == "I" or argImg.mode == "RGBA":
                argImg.save(argPath, "png", compress_level=argPBlock.pngcompressionlevel)
            else:
                argImg.convert(mode="RGB", dither=Image.Dither.NONE).save(argPath, "png", compress_level=argPBlock.pngcompressionlevel)
        else:
            argImg.convert(mode="L", dither=Image.Dither.NONE).save(argPath, "png", compress_level=argPBlock.pngcompressionlevel)

def checkColor(argImg: Image, argPBlock) -> bool:
    imgBands = argImg.split()
    return ImageChops.difference(imgBands[0], imgBands[1]).getextrema()[1] > argPBlock.rgbcolorthreshold or \
            ImageChops.difference(imgBands[1], imgBands[2]).getextrema()[1] > argPBlock.rgbcolorthreshold

def getResultFilePath(argSourceFilePath: pathlib.Path, argExtension: str, argPBlock) -> pathlib.Path:
    return argPBlock.output.with_name(argPBlock.output.name + argExtension.title()) / argSourceFilePath.parent.name \
          / (argSourceFilePath.stem + "." + argExtension.lower())
        
def getResampleSize(argX: int, argY: int, argPBlock):
    if argPBlock.resizefit:
        tmpRatio = min(argPBlock.resizewidth / argX, argPBlock.resizeheight / argY)
        if tmpRatio < 1:
            return (round(argX * tmpRatio), round(argY * tmpRatio))
        else:
            return (argX, argY)
    else:
        if argX < argY: #Image is vertical
            internalTarget = argPBlock.verticalresizetarget
        else: #Image is horizontal
            internalTarget = argPBlock.horizontalresizetarget

        if argX > internalTarget:
            tmpRatio = internalTarget / argX
            newY = round(argY * tmpRatio)
            return (internalTarget, newY)
        else:
            return (argX, argY)

def cropUniversal(argImgArray: numpy.ndarray, argPBlock, argVertical: bool, argExhaustive: bool, argReverse: bool):
    emptyLinesList = list()

    internalAxis1, internalAxis2 = int(not argVertical), int(argVertical)    

    if argExhaustive:
        internalRange = range(0, numpy.size(argImgArray, internalAxis1))
        internalSize1 = numpy.size(argImgArray, internalAxis1)
    else:
        if argReverse:
            internalRange = range(numpy.size(argImgArray, internalAxis1) - 1, -1, -1)
        else:
            internalRange = range(0, numpy.size(argImgArray, internalAxis1))
    internalSize2 = numpy.size(argImgArray, internalAxis2)   

    for coord in internalRange:
        if argVertical:
            vals, freq = numpy.unique(argImgArray[coord, :], return_counts=True)
        else:
            vals, freq = numpy.unique(argImgArray[:, coord], return_counts=True)
        backColor = vals[numpy.argmax(freq)]
        newFuzzyCount = numpy.sum(numpy.take(freq, numpy.where(numpy.logical_and(vals > (backColor - argPBlock.colordistance), vals < (backColor + argPBlock.colordistance)))))
        lineFuzzyError = round((1 - newFuzzyCount / internalSize2) * 100, 4)
        if lineFuzzyError < argPBlock.fillratio:
            emptyLinesList.append(coord)
        else:
            if not argExhaustive:
                return emptyLinesList, lineFuzzyError
            
    if argExhaustive:
        if len(emptyLinesList) == internalSize1:
            emptyLinesList.clear()
    else:
        emptyLinesList.clear()
    return emptyLinesList, -1

def workerEntryPoint(argWorkerArgs):
    argImageFilePath, wkArgs = argWorkerArgs
    errorMessagesList = list()
    debugMessagesList = list()

    try:
        im = Image.open(argImageFilePath)
    except:
        errorMessagesList.append(f"Error opening {argImageFilePath}.")
        return argImageFilePath, debugMessagesList, errorMessagesList
    
    debugMessagesList.append(f"Cropping {argImageFilePath.as_posix()}")
    debugMessagesList.append(f"{im.format} {im.size} {im.mode}")
    
    if "icc_profile" in im.info:
        debugMessagesList.append("Applying color profile.")
        f = io.BytesIO(im.info["icc_profile"])
        im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(f), ImageCms.createProfile("sRGB"), outputMode="RGB")

    imArray = numpy.asarray(im)
    if im.mode == "L":
        imWkArray = numpy.copy(imArray)
    else:
        imWkArray = numpy.asarray(im.convert(mode="L", dither=Image.Dither.NONE))

    topLinesList, topFuzzyError = cropUniversal(imWkArray, wkArgs, True, False, False)
    bottomLinesList, bottomFuzzyError = cropUniversal(imWkArray, wkArgs, True, False, True)

    if len(topLinesList) == 0:
        if topFuzzyError == -1:
            debugMessagesList.append("Fast crop (top): probably empty image")
        else:
            debugMessagesList.append(f"Fast crop (top): nothing cropped, Fr: {topFuzzyError}%")
    else:
        debugMessagesList.append(f"Fast crop (top): content line {topLinesList[-1]}, Fe: {topFuzzyError}%")
    if len(bottomLinesList) == 0:
        if bottomFuzzyError == -1:
            debugMessagesList.append("Fast crop (bottom): probably empty image")
        else:
            debugMessagesList.append(f"Fast crop (bottom): nothing cropped, Fr: {bottomFuzzyError}%")  
    else:
        debugMessagesList.append(f"Fast crop (bottom): content line {bottomLinesList[-1]}, Fr: {bottomFuzzyError}%")
    imArray = numpy.delete(imArray, topLinesList + bottomLinesList, 0)

    leftLinesList, leftFuzzyError = cropUniversal(imWkArray, wkArgs, False, False, False)
    rightLinesList, rightFuzzyError = cropUniversal(imWkArray, wkArgs, False, False, True)

    if ((len(leftLinesList) + len(rightLinesList)) / im.size[0]) * 100 > wkArgs.exhaustivethreshold:
        if len(leftLinesList) == 0:
            if leftFuzzyError == -1:
                debugMessagesList.append("Fast crop (left): probably empty image")
            else:
                debugMessagesList.append(f"Fast crop (left): nothing cropped, Fr: {leftFuzzyError}%")  
        else:
            debugMessagesList.append(f"Fast crop (left): content line {leftLinesList[-1]}, Fr: {leftFuzzyError}%")
        if len(rightLinesList) == 0:
            if leftFuzzyError == -1:
                debugMessagesList.append("Fast crop (right): probably empty image")
            else:
                debugMessagesList.append(f"Fast crop (right): nothing cropped, Fr: {rightFuzzyError}%")  
        else:
            debugMessagesList.append(f"Fast crop (right): content line {rightLinesList[-1]}, Fr: {rightFuzzyError}%")
        imArray = numpy.delete(imArray, leftLinesList + rightLinesList, 1)
    else:
        lineListH, dummyFuzzyError = cropUniversal(imWkArray, wkArgs, False, True, False)
        debugMessagesList.append(f"Exhaustive crop (horizontal): {len(lineListH)} lines matched")
        imArray = numpy.delete(imArray, lineListH, 1)

    imCrop = Image.fromarray(imArray, im.mode)
    if im.mode == "P":
        imCrop.putpalette(im.getpalette())

    if wkArgs.resizefit or wkArgs.resizefitwidth:
        imResize = imCrop.resize(getResampleSize(imCrop.size[0], imCrop.size[1], wkArgs), Image.Resampling.LANCZOS)
    else:
        imResize = imCrop

    currentResultPng = getResultFilePath(argImageFilePath, "png", wkArgs)
    os.makedirs(currentResultPng.parent, exist_ok=True)
    savePng(imResize, currentResultPng, wkArgs)

    if wkArgs.encodeavif:
        currentResultAvif = getResultFilePath(argImageFilePath, "avif", wkArgs)
        os.makedirs(currentResultAvif.parent, exist_ok=True)
        encodeProcess = subprocess.run(getAvifCmdline(currentResultPng, currentResultAvif, wkArgs), \
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        if encodeProcess.returncode != 0:
            errorMessagesList.append("Possible error in AVIF encoding")
            errorMessagesList.append(f"Avifenc stderr: {encodeProcess.stderr}")
        else:
            debugMessagesList.append("Encoded to AVIF")

    return argImageFilePath, debugMessagesList, errorMessagesList

def main():
    imageFileList = list()
    args = getParameterBlock()

    try:
        logging.basicConfig(filename=args.logpath, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8", level=logging.DEBUG)
    except FileNotFoundError:
        print("Can't setup logging due to invalid path. Logging is disabled.")

    print(f"Autocrop script {ver}")
    logging.info(f"Autocrop script {ver}")

    logging.info(f"Fill ratio: {str(args.fillratio)}%")
    logging.info(f"Color distance: {str(args.colordistance)} colors")
    logging.info(f"Input file/directory: {args.input.as_posix()}")
    logging.info(f"Output template: {args.output.as_posix()}")

    if pathlib.Path.is_file(args.input):
        logging.info("Single file mode")
        imageFileList.append((args.input, args))
    else:
        for currentImageFilePath in args.input.rglob("*"):
            if currentImageFilePath.is_file and \
                    (currentImageFilePath.suffix == ".jpg" or currentImageFilePath.suffix == ".jpeg" or currentImageFilePath.suffix == ".png"):
                imageFileList.append((currentImageFilePath, args))
        logging.info(f"Converting {len(imageFileList)} files")
    
    gotErrors = False
    with multiprocessing.Pool() as wkPool:
        wkResults = wkPool.imap_unordered(workerEntryPoint, imageFileList)
        for wkFile, wkDebugMessagesList, wkErrorMesssagesList in wkResults:
            if len(wkErrorMesssagesList) != 0:
                gotErrors = True
            print(f"Converting {wkFile.as_posix()}")
            for currentDebugLine in wkDebugMessagesList:   
                logging.info(currentDebugLine)
            for currentErrorLine in wkErrorMesssagesList:
                logging.error(currentErrorLine)

    if gotErrors:
        print("!! Errors may have occured. Please check log.")

    logging.info("Autocrop is finished")
    print("Autocrop is finished")

    if not args.nowait:
        input("Press enter to exit.")
    
if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
