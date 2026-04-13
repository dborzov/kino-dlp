---
title: "Local Media Assets"
source: "https://support.plex.tv/articles/200220677-local-media-assets-movies/#toc-1"
author:
published: 2013-09-19
created: 2026-04-13
description: "The scanners and metadata agents used by Plex will work best when your major types of content are separated from..."
tags:
  - "clippings"
---


You might have your own image files for movie posters & backgrounds, subtitles, your own movie “extras”, etc. To use these, ensure they are named and organized, and that the Local Media Assets source is enabled and ordered correctly.

## Enable Local Assets Plex Movie Agent

If using the new **Plex Movie** agent you only need to enable “Use local Assets” in the libraries advanced settings

**Related Page:** [Advanced settings Plex Movie Agent](https://support.plex.tv/articles/advanced-settings-plex-movie-agent/)

## Enable “Local Media Assets” (Legacy Agents)

if using the legacy *Personal Media*, *Plex Movie (Legacy)* or *The Movie Database* agents”Local Media Assets” is an Agent source that loads local media files or embedded metadata for a media item. To do this, ensure the Agent source is enabled and topmost in the list:

- Launch the Plex Web App
- Choose Settings from the top right of the Home screen
- Select your Plex Media Server from the settings sidebar
- Choose Agents
- Choose the Library Type and Agent you want to change
- Ensure Local Media Assets is checked
- Ensure Local Media Assets is topmost in the list

**Related Page**: [Metadata Agents](https://support.plex.tv/articles/200241558-agents/)

## Local Artwork Assets

### Supported Artwork Image Formats

There are a number of supported custom media items that need to be named correctly so they are detected. The supported image file formats are:

- jpg
- jpeg
- png
- tbn

### Poster Artwork

Posters are typically displayed for movies on Plex app dashboards, library views, and when looking at details for the movie. Poster art is typically of 1:1.5 aspect ratio. Custom Poster artwork will be detected and used if named and stored as follows:

- `MovieName (Release Date).ext` or
- `Movie/MovieName (Release Date)/Custom_Poster_Name.ext`

Where `Custom_Poster_Name` is:

- cover
- default
- folder
- movie
- poster

…and `ext` is the file extension. (Some operating systems such as Windows may [hide your file extensions](http://www.thewindowsclub.com/show-file-extensions-in-windows) by default.)

```js
/Movies
   /Batman Begins (2005)
      Batman Begins (2005).mkv
      poster.jpg
```

#### Multiple Poster Images

More than one poster image can be included. The poster used can be selected in the Plex Web App. For multiple items to be scanned, they should be named as follows:

- `Custom_Poster_Name-X.ext`

Where `-X` is a number

```js
/Movies
   /Avatar (2009)
      Avatar (2009).mkv
      poster.jpg
      poster-2.png
   Batman Begins (2005).mkv
   Batman Begins (2005)-1.jpg
   Batman Begins (2005)-2.tbn
```

### Clear Logos

Clear Logos are the movie title art that is used in modern Plex clients detail screens. PNG files are recommended as they can have a transparent surrounding area ([alpha channel](https://en.wikipedia.org/wiki/Alpha_compositing)) so that the image or colors behind it can show through.

TV Shows/Movie Name (year)/clearlogo.png

- clearlogo.ext
- logo.ext
```js
/Movies
   Avatar (2009)-clearlogo.png
   Avatar (2009).mkv
```

OR

```js
/Movies
   /Avatar (2009)
      clearlogo.png
      Avatar (2009).mkv
```

##### Multiple Clear Logos

More than one clear logo image can be included. The clear logo used can be selected in the Plex Web App. For multiple items to be scanned, they should be named as follows:

- clearlogo-X.ext
- logo-X.ext

Where `-X` is a number.

```js
/Movies
   /Avatar (2009)
      clearlogo.png
      clearlogo-1.png
      clearlogo-2.png
      Avatar (2009).mkv
```

### Background (Fanart) Artwork

Background art is often displayed in the background when looking at the details page for a movie. It can also be used in the background elsewhere or for a slideshow or screensaver. Background art typically uses a 16:9 aspect ratio. Local background artwork or “fanart” can be specified as follows:

- `MovieName (Release Date)-fanart.ext` or
- `Movies/MovieName (Release Date)/Custom_Fanart_Name.ext`

Where `Custom_Fanart_Name` can be one of the following:

- art
- backdrop
- background
- fanart
```js
/Movies
   Avatar (2009)-fanart.jpg
   Avatar (2009).mkv
```

OR

```js
/Movies
   /Avatar (2009)
      Avatar (2009).mkv
      fanart.jpg
```

#### Multiple Background (Fanart) Images

More than one Fanart image can be included. The Fanart image used can be selected in the Plex Web App. For multiple items to be scanned, they should be named as follows:

- `Custom_Fanart_Name-X.ext`

Where `-X` is a number

```js
/Movies
   /Avatar (2009)
      Avatar (2009).mkv
      fanart.jpg
      fanart-2.png
   Batman Begins (2005).mkv
   Batman Begins (2005)-fanart-1.jpg
   Batman Begins (2005)-fanart-2.tbn
```

### Square Art

Square art is the background image used on the details screens of movies on the iOS and Android mobile apps.

- `MovieName (Release Date)-Custom_SquareArt_Name.ext` or
- `Movies/MovieName (Release Date)/Custom_SquareArt_Name.ext`

Where `Custom_SquareArt_Name` can be:

- square.ext
- squareArt.ext
- backgroundSquare.ext
```js
/Movies
   Avatar (2009)-squareArt.jpg
   Avatar (2009).mkv
```

OR

```js
/Movies
   /Avatar (2009)
      backgroundSquare.jpg
      Avatar (2009).mkv
```

## External Subtitle Files

Several formats of subtitle files are supported and can be picked up by the Local Media Assets scanner:

- SRT
- SMI
- SSA (or ASS)

Other formats such as VOBSUB, PGS, etc. may work on some Plex apps but not all.

Subtitle files need to be named as follows:

- `MovieName (Release Date).[Language_Code].ext` or
- `Movies/MovieName (Release Date).[Language_Code].ext`
- `Movies/MovieName (Release Date).[Language_Code].forced.ext`

Where `[Language_Code]` is defined by the ISO-639-1 (2-letter) or ISO-639-2/B (3-letter) standard.

**Related Page**: [ISO-639-1 codes](http://en.wikipedia.org/wiki/List_of_ISO_639-1_codes "Wikipedia: ISO-639-1 codes") (2-letter)  
**Related Page**: [ISO-639-2/B codes](http://en.wikipedia.org/wiki/List_of_ISO_639-2_codes "Wikipedia: ISO-639-2/B codes") (3-letter)

```js
/Movies
   Avatar (2009).mkv
   Avatar (2009).en.srt
```

OR

```js
/Movies
   /Avatar (2009)
      Avatar (2009).mkv
      Avatar (2009).eng.ass
```

**Note**: If the language code is not added, Plex apps will show “Unknown” instead of the subtitle language and the automatic process which determines if the subtitle should be shown or not will not work as intended.

**Tip!**:”forced” is a special tag which make the subtitle enabled even if it does not necessarily follow the rules set in your server’s language settings. Normally used for subs which only contain the translation for foreign parts.

## Local Trailers and Extras

If you have trailers, interviews, behind the scenes videos, or other “extras” type content for your movies, you can add those.

**Related Page**: [Local Files for Movie Trailers and Extras](https://support.plex.tv/articles/local-files-for-trailers-and-extras/)