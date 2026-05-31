package handler

import (
	"net/http"

	"github.com/tts-platform/api/internal/gen"
)

func voiceMeta(id, label, hint string) gen.VoiceMeta {
	return gen.VoiceMeta{Id: &id, Label: &label, Hint: &hint}
}

func tagMeta(tag, label string) gen.EmotiveTagMeta {
	return gen.EmotiveTagMeta{Tag: &tag, Label: &label}
}

var voicesMeta = []gen.VoiceMeta{
	voiceMeta("tara", "Tara", "Female, conversational, clear"),
	voiceMeta("leah", "Leah", "Female, warm, gentle"),
	voiceMeta("jess", "Jess", "Female, energetic, youthful"),
	voiceMeta("leo", "Leo", "Male, authoritative, deep"),
	voiceMeta("dan", "Dan", "Male, friendly, casual"),
	voiceMeta("mia", "Mia", "Female, professional, articulate"),
	voiceMeta("zac", "Zac", "Male, enthusiastic, dynamic"),
	voiceMeta("zoe", "Zoe", "Female, calm, soothing"),
}

var emotiveTagsMeta = []gen.EmotiveTagMeta{
	tagMeta("<laugh>", "Laugh"),
	tagMeta("<chuckle>", "Chuckle"),
	tagMeta("<sigh>", "Sigh"),
	tagMeta("<cough>", "Cough"),
	tagMeta("<sniffle>", "Sniffle"),
	tagMeta("<groan>", "Groan"),
	tagMeta("<yawn>", "Yawn"),
	tagMeta("<gasp>", "Gasp"),
}

func (s *Server) GetMetaVoices(w http.ResponseWriter, r *http.Request) {
	notes := "English voices for the current model. Insert emotive tags in text; speech is formatted as {voice}: your text."
	writeJSON(w, http.StatusOK, gen.VoicesMetaResponse{
		Voices:      &voicesMeta,
		EmotiveTags: &emotiveTagsMeta,
		Notes:       &notes,
	})
}
